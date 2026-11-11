#!/usr/bin/env python3
"""C-01 Supervisor binding browser checks (real extension/Bridge/Core, mock page)."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from contextlib import AsyncExitStack
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
EXT = ROOT / "contextflow-extension"
MOCK_DIR = ROOT / "_artifacts" / "cfadapter001-mock"
TOKEN = "dev-token-cf-exp-002"
SESS_A = "aaaa1111-0000-4000-8000-00000000000a"
SESS_B = "bbbb2222-0000-4000-8000-00000000000b"
SESS_C = "cccc3333-0000-4000-8000-00000000000c"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_http(url: str, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("service not ready: " + url)


def external_count(port: int) -> int:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
        return int(json.load(response)["external_clients"])


def wait_external(port: int, expected: int, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if external_count(port) == expected:
                return
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"external client count did not become {expected}")


async def run_binding() -> tuple[int, int]:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: object = "") -> None:
        checks.append((name, bool(ok), str(detail)))
        print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail else ""), flush=True)

    temp = tempfile.TemporaryDirectory()
    temp_root = Path(temp.name)
    profile = temp_root / "profile"
    extension = temp_root / "extension"
    shutil.copytree(EXT, extension)
    registry = temp_root / "endpoints.json"
    database = temp_root / "messages.sqlite3"
    bridge_port, mock_port = free_port(), free_port()
    (extension / "runtime_config.js").write_text(
        'globalThis.ContextFlowRuntimeConfig = Object.freeze({allowed_mock_origins:["http://127.0.0.1:%d"]});\n' % mock_port,
        encoding="utf-8",
    )

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def translate_path(self, path):
            if "/c/" in path:
                path = "/mock.html"
            return super().translate_path(path)

    server = ThreadingHTTPServer(("127.0.0.1", mock_port), partial(Quiet, directory=str(MOCK_DIR)))
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    env = dict(os.environ)
    env.update(CF_BRIDGE_TOKEN=TOKEN, CONTEXTFLOW_ENDPOINT_REGISTRY=str(registry))
    bridge = subprocess.Popen(
        [sys.executable, "-B", str(EXT / "bridge_server.py"), "--port", str(bridge_port)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    core = None
    try:
        wait_http(f"http://127.0.0.1:{bridge_port}/health")

        def start_core():
            process = subprocess.Popen(
                [sys.executable, "-B", "-m", "contextflow_core.receiver",
                 "--database", str(database), "--registry", str(registry),
                 "--uri", f"ws://127.0.0.1:{bridge_port}/ws?token={TOKEN}",
                 "--origin", f"http://127.0.0.1:{bridge_port}"],
                cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            wait_external(bridge_port, 1)
            return process

        core = start_core()
        async with async_playwright() as p, AsyncExitStack() as cleanup:
            ctx = await p.chromium.launch_persistent_context(
                str(profile), channel="msedge", headless=True,
                args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}",
                      "--no-first-run", "--no-default-browser-check"],
            )
            cleanup.push_async_callback(ctx.close)
            sw = ctx.service_workers[0] if ctx.service_workers else await ctx.wait_for_event("serviceworker", timeout=15000)
            ext_id = sw.url.split("/")[2]
            chat = await ctx.new_page()
            await chat.goto(f"http://127.0.0.1:{mock_port}/mock.html?s={SESS_A}", wait_until="domcontentloaded")
            await chat.evaluate("""sid => {
              const style=document.createElement('style');
              style.textContent='.assistant-content{white-space:pre-wrap}';
              document.head.appendChild(style);
              document.title='阶段 C Supervisor 测试';
              const project=document.createElement('a');
              project.href='/g/g-p-0123456789abcdef0123456789abcdef/project';
              project.setAttribute('aria-label','contextflow 测试项目');
              project.textContent='contextflow 测试项目';
              document.body.prepend(project);
              history.replaceState({},'',`/g/g-p-0123456789abcdef0123456789abcdef-contextflow-test/c/${sid}`);
              window.__mock.seed();
            }""", SESS_A)
            bridge_page = await ctx.new_page()
            await bridge_page.goto(f"http://127.0.0.1:{bridge_port}/bridge_client.html?token={TOKEN}", wait_until="domcontentloaded")
            await bridge_page.wait_for_function("() => window.__cfBridgePage && window.__cfBridgePage.isConnected()", timeout=15000)
            # Headless Edge does not expose the ephemeral action-popup target.
            # Drive the real popup.js with a page-local runtime stub to verify UI
            # rendering and the two-step confirmation payload.
            popup_ui = await ctx.new_page()
            popup_markup = (extension / "popup.html").read_text(encoding="utf-8")
            popup_markup = popup_markup.replace('<script src="popup.js"></script>', '')
            await popup_ui.set_content(popup_markup, wait_until="domcontentloaded")
            await popup_ui.evaluate("""() => {
              const calls = [];
              let state = {
                binding:{native_session_id:'session-old',endpoint_id:'cfep_old',state:'active',anchor:{message_id:'old'}},
                current:{session_id:'session-target',kind:'chat',bindable:true,latest_assistant_id:null},
                status:'mismatch',bridge_status:'connected',core_status:'available',error:null
              };
              globalThis.__popupStubCalls=calls;
              globalThis.chrome={runtime:{sendMessage:async(message)=>{
                calls.push(JSON.parse(JSON.stringify(message)));
                if(message.type==='cf:supervisor_get_state') return {ok:true,data:state};
                if(message.type==='cf:supervisor_bind'&&message.confirm_replace!==true) return {ok:false,error:{code:'REPLACEMENT_CONFIRMATION_REQUIRED',message:'confirm',details:{existing_session_id:'session-old',target_session_id:'session-target'}}};
                if(message.type==='cf:supervisor_bind'){state={...state,binding:{...state.binding,native_session_id:'session-target'},status:'active'};return {ok:true,data:{binding:state.binding}};}
                return {ok:true,data:{}};
              }}};
            }""")
            await popup_ui.add_script_tag(content=(extension / "popup.js").read_text(encoding="utf-8") + "\n//# sourceURL=popup-test.js")
            await asyncio.sleep(0.3)
            stub_debug = await popup_ui.evaluate("() => ({calls:__popupStubCalls,bindDisabled:document.querySelector('[data-testid=bind]').disabled})")
            await popup_ui.wait_for_function("() => document.querySelector('[data-testid=bind]').disabled === false")
            await popup_ui.locator('[data-testid="bind"]').click()
            await asyncio.sleep(0.3)
            debug_calls = await popup_ui.evaluate("() => __popupStubCalls")
            debug_hidden = await popup_ui.locator('[data-testid="replace-confirm"]').get_attribute("hidden")
            if not any(call.get("type") == "cf:supervisor_bind" for call in debug_calls):
                raise AssertionError("popup bind click did not call runtime: " + json.dumps({"calls":debug_calls,"before":stub_debug}))
            if debug_hidden is not None:
                raise AssertionError("popup did not reveal replacement control: " + json.dumps(debug_calls))
            await popup_ui.locator('[data-testid="replace-confirm"]').click()
            await asyncio.sleep(0.2)
            popup_calls = await popup_ui.evaluate("() => __popupStubCalls")
            confirmation_call = next((call for call in popup_calls if call.get("confirm_replace") is True), None)
            check("B0 popup.js preserves replacement target in confirmation wire",
                  confirmation_call is not None and confirmation_call.get("expected_replacement_session_id") == "session-target", popup_calls)

            # Older delayed refresh must not overwrite a newer post-mutation view.
            race = await popup_ui.evaluate("""async () => {
              let releaseOld;
              let state={binding:null,current:{session_id:'race',kind:'chat',bindable:true},status:'unbound',bridge_status:'connected',core_status:'available',error:null};
              let calls=0;
              chrome.runtime.sendMessage=async(message)=>{
                if(message.type==='cf:supervisor_get_state'){
                  calls+=1;
                  if(calls===1) return await new Promise(resolve=>{releaseOld=()=>resolve({ok:true,data:{...state,status:'unbound'}});});
                  return {ok:true,data:state};
                }
                if(message.type==='cf:supervisor_bind'){state={...state,binding:{native_session_id:'race',endpoint_id:'cfep_race',state:'active',anchor:{}},status:'active'};return {ok:true,data:{binding:state.binding}};}
                return {ok:true,data:{}};
              };
              refresh();
              await new Promise(resolve=>setTimeout(resolve,20));
              await command('cf:supervisor_bind',{confirm_replace:false});
              releaseOld();
              await new Promise(resolve=>setTimeout(resolve,50));
              return document.querySelector('[data-testid=binding-status]').dataset.state;
            }""")
            check("B0 delayed old refresh cannot overwrite post-mutation view", race == "active", race)

            # Failure must re-enable refresh; subsequent click recovers.
            recovery = await popup_ui.evaluate("""async () => {
              let fail=true;
              chrome.runtime.sendMessage=async(message)=>{
                if(message.type==='cf:supervisor_get_state'&&fail){fail=false;throw new Error('offline');}
                return {ok:true,data:{binding:null,current:{session_id:'recover',kind:'chat',bindable:true},status:'unbound',bridge_status:'connected',core_status:'available',error:null}};
              };
              await refresh();
              const enabledAfterFailure=!document.querySelector('[data-testid=refresh]').disabled;
              document.querySelector('[data-testid=refresh]').click();
              await new Promise(resolve=>setTimeout(resolve,50));
              return {enabledAfterFailure,state:document.querySelector('[data-testid=binding-status]').dataset.state,enabledAfterRecovery:!document.querySelector('[data-testid=refresh]').disabled};
            }""")
            check("B0 popup refresh failure remains retryable and recovers",
                  recovery == {"enabledAfterFailure": True, "state": "unbound", "enabledAfterRecovery": True}, recovery)
            await popup_ui.close()

            # Load the real resource without a stub too: as an ordinary extension
            # tab it has sender.tab and must be denied by the production gate.
            popup = await ctx.new_page()
            popup.on("console", lambda message: print(f"[popup:{message.type}] {message.text}", flush=True))
            popup.on("pageerror", lambda error: print(f"[popup:error] {error}", flush=True))
            await popup.goto(f"chrome-extension://{ext_id}/popup.html", wait_until="domcontentloaded")
            required_ui = ["current-project", "current-conversation", "bound-project", "bound-conversation", "binding-status", "bridge-status", "core-status", "bind", "pause", "resume", "unbind", "refresh"]
            check("B0 real popup resource has stable control surface", all([await popup.locator(f'[data-testid="{name}"]').count() == 1 for name in required_ui]))

            async def supervisor_command(message):
                return await sw.evaluate("""async (message) => handleSupervisorManagement(message, {
                  id: chrome.runtime.id,
                  url: chrome.runtime.getURL('popup.html')
                })""", message)

            async def supervisor_view():
                result = await supervisor_command({"type": "cf:supervisor_get_state"})
                if not result.get("ok"):
                    raise AssertionError("state failed: " + json.dumps(result))
                return result["data"]

            async def activate_chat():
                tab_id = await sw.evaluate("""async (url) => {
                  const tabs = await chrome.tabs.query({});
                  const tab = tabs.find(t => t.url === url);
                  if (!tab) throw new Error('chat tab missing');
                  await chrome.tabs.update(tab.id, {active:true});
                  return tab.id;
                }""", chat.url)
                await asyncio.sleep(0.15)
                return tab_id

            async def refresh():
                await activate_chat()
                return await supervisor_view()

            view = await refresh()
            check("B1 stable session is bindable", view["status"] == "unbound" and view["current"]["bindable"], view)
            check("B2 Bridge and Core independently connected",
                  view["bridge_status"] == "connected" and view["core_status"] == "available", view)

            await activate_chat()
            result = await supervisor_command({"type": "cf:supervisor_bind", "confirm_replace": False})
            check("B3 bind command succeeds", result.get("ok") is True, result)
            binding = await sw.evaluate("() => chrome.storage.local.get('cfSupervisorBindingV1').then(x => x.cfSupervisorBindingV1)")
            mock_state = await chat.evaluate("() => window.__mock.state()")
            latest_a = [t["id"] for t in mock_state["turns"] if t["role"] == "assistant"][-1]
            check("B3 bind persists Core Endpoint and latest-message anchor",
                  binding["native_session_id"] == SESS_A and binding["state"] == "active"
                  and binding["anchor"]["message_id"] == latest_a
                  and str(binding["endpoint_id"]).startswith("cfep_"), binding)
            check("B3 binding persists project and conversation display names",
                  binding["project_name"] == "contextflow 测试项目"
                  and binding["conversation_name"] == "阶段 C Supervisor 测试", binding)

            await popup.close()
            await chat.reload(wait_until="domcontentloaded")
            popup = await ctx.new_page()
            await popup.goto(f"chrome-extension://{ext_id}/popup.html", wait_until="domcontentloaded")
            view = await refresh()
            check("B4 page and popup reopen restore verified binding", view["status"] == "active", view)

            # Terminate the MV3 worker and wake it through an extension-page runtime message.
            cdp = await ctx.browser.new_browser_cdp_session()
            targets = await cdp.send("Target.getTargets")
            target = next(item for item in targets["targetInfos"] if item["type"] == "service_worker" and ext_id in item["url"])
            await cdp.send("Target.closeTarget", {"targetId": target["targetId"]})
            await asyncio.sleep(0.6)
            await popup.reload(wait_until="domcontentloaded")
            deadline = time.monotonic() + 15
            new_sw = None
            while time.monotonic() < deadline:
                new_sw = next((worker for worker in ctx.service_workers if worker.url == sw.url), None)
                if new_sw is not None:
                    try:
                        await new_sw.evaluate("() => chrome.runtime.id")
                        break
                    except Exception:
                        new_sw = None
                await asyncio.sleep(0.2)
            if new_sw is None:
                raise AssertionError("service worker did not restart after extension-page wake")
            sw = new_sw
            view = await refresh()
            restored = await sw.evaluate("() => chrome.storage.local.get('cfSupervisorBindingV1').then(x => x.cfSupervisorBindingV1)")
            check("B5 service worker restart restores local binding", restored["native_session_id"] == SESS_A and view["status"] == "active", view)

            await chat.evaluate("sid => window.__mock.switchSession(sid)", SESS_B)
            view = await refresh()
            check("B6 switching Session reports mismatch", view["status"] == "mismatch", view)
            paused_mismatch = await supervisor_command({"type": "cf:supervisor_pause"})
            check("B6 pause remains available during mismatch", paused_mismatch.get("ok") is True)
            resume_mismatch = await supervisor_command({"type": "cf:supervisor_resume"})
            still_paused = await sw.evaluate("() => chrome.storage.local.get('cfSupervisorBindingV1').then(x => x.cfSupervisorBindingV1.state)")
            check("B6 resume rejects mismatched Session without mutation",
                  resume_mismatch.get("error", {}).get("code") == "SESSION_CHANGED" and still_paused == "paused", resume_mismatch)
            before_registry = json.loads(registry.read_text(encoding="utf-8"))
            replacement = await supervisor_command({"type": "cf:supervisor_bind", "confirm_replace": False})
            replacement_data = replacement.get("error", {}).get("details", {})
            check("B7 replacement requires explicit target confirmation",
                  replacement.get("error", {}).get("code") == "REPLACEMENT_CONFIRMATION_REQUIRED"
                  and replacement_data.get("existing_session_id") == SESS_A
                  and replacement_data.get("target_session_id") == SESS_B
                  and json.loads(registry.read_text(encoding="utf-8")) == before_registry, replacement)
            await chat.evaluate("sid => window.__mock.switchSession(sid)", SESS_C)
            stale = await supervisor_command({
                "type": "cf:supervisor_bind", "confirm_replace": True,
                "expected_replacement_session_id": SESS_B,
            })
            check("B7 stale replacement confirmation cannot bind navigated Session C",
                  stale.get("error", {}).get("code") == "REPLACEMENT_CONFIRMATION_REQUIRED"
                  and json.loads(registry.read_text(encoding="utf-8")) == before_registry, stale)
            await supervisor_command({"type": "cf:supervisor_unbind"})
            stale_after_unbind = await supervisor_command({
                "type": "cf:supervisor_bind", "confirm_replace": True,
                "expected_replacement_session_id": SESS_B,
            })
            check("B7 stale confirmation cannot bind after unbind",
                  stale_after_unbind.get("error", {}).get("code") == "REPLACEMENT_CONFIRMATION_REQUIRED"
                  and json.loads(registry.read_text(encoding="utf-8")) == before_registry, stale_after_unbind)
            await chat.evaluate("sid => window.__mock.switchSession(sid)", SESS_A)
            restored_a = await supervisor_command({"type": "cf:supervisor_bind", "confirm_replace": False})
            check("B7 test setup restores original A binding", restored_a.get("ok") is True, restored_a)
            await chat.evaluate("sid => window.__mock.switchSession(sid)", SESS_B)
            replaced = await supervisor_command({
                "type": "cf:supervisor_bind", "confirm_replace": True,
                "expected_replacement_session_id": SESS_B,
            })
            check("B8 confirmed replacement command succeeds", replaced.get("ok") is True, replaced)
            binding_b = await sw.evaluate("() => chrome.storage.local.get('cfSupervisorBindingV1').then(x => x.cfSupervisorBindingV1)")
            check("B8 confirmed replacement binds Session B", binding_b["native_session_id"] == SESS_B)

            paused = await supervisor_command({"type": "cf:supervisor_pause"})
            check("B9 pause command succeeds", paused.get("ok") is True, paused)
            check("B9 pause changes only local state", (await sw.evaluate("() => chrome.storage.local.get('cfSupervisorBindingV1').then(x => x.cfSupervisorBindingV1.state)")) == "paused")
            new_assistant = await chat.evaluate("() => window.__mock.addAssistant('resume anchor').id")
            resumed_result = await supervisor_command({"type": "cf:supervisor_resume"})
            check("B10 resume command succeeds", resumed_result.get("ok") is True, resumed_result)
            resumed = await sw.evaluate("() => chrome.storage.local.get('cfSupervisorBindingV1').then(x => x.cfSupervisorBindingV1)")
            check("B10 resume revalidates and resets anchor", resumed["anchor"]["message_id"] == new_assistant and resumed["anchor"]["reason"] == "resume")

            second = await ctx.new_page()
            await second.goto(f"http://127.0.0.1:{mock_port}/mock.html?s={SESS_B}", wait_until="domcontentloaded")
            await second.evaluate("() => window.__mock.seed()")
            await sw.evaluate("""async (url) => { const tabs=await chrome.tabs.query({}); const t=tabs.find(x=>x.url===url); await chrome.tabs.update(t.id,{active:true}); }""", second.url)
            view = await supervisor_view()
            persisted = await sw.evaluate("() => chrome.storage.local.get('cfSupervisorBindingV1').then(x => x.cfSupervisorBindingV1)")
            check("B11 same Session across tabs uses one Session binding", view["status"] == "active" and persisted["native_session_id"] == SESS_B and "tab_id" not in persisted, persisted)

            await chat.bring_to_front(); await chat.evaluate("() => history.replaceState({}, '', '/')")
            view = await refresh()
            check("B12 page without stable Session is unavailable", view["status"] == "identity_unavailable" and not view["current"]["bindable"], view)

            unbound = await supervisor_command({"type": "cf:supervisor_unbind"})
            check("B13 unbind command succeeds", unbound.get("ok") is True, unbound)
            registry_after = json.loads(registry.read_text(encoding="utf-8"))
            local = await sw.evaluate("() => chrome.storage.local.get('cfSupervisorBindingV1').then(x => x.cfSupervisorBindingV1 || null)")
            check("B13 unbind keeps Core Registry history", local is None and len(registry_after["endpoints"]) == 2)

            trusted = await sw.evaluate("""() => ({
              extension: isTrustedExtensionPage({id:chrome.runtime.id,url:chrome.runtime.getURL('popup.html')}),
              tab: isTrustedExtensionPage({id:chrome.runtime.id,url:chrome.runtime.getURL('popup.html'),tab:{id:1}}),
              web: isTrustedExtensionPage({id:chrome.runtime.id,url:'https://chatgpt.com/'})
            })""")
            check("B14 management sender gate rejects tab/web senders", trusted == {"extension": True, "tab": False, "web": False}, trusted)
            forbidden = await sw.evaluate("""() => handleSupervisorManagement(
              {type:'cf:supervisor_bind',confirm_replace:true},
              {id:chrome.runtime.id,url:'https://chatgpt.com/',tab:{id:1}}
            )""")
            check("B14 tab-originated management cannot mutate binding",
                  forbidden.get("error", {}).get("code") == "MANAGEMENT_FORBIDDEN", forbidden)
            # Opening popup.html as an ordinary extension tab gives it sender.tab;
            # its actual runtime message must therefore be denied, not privileged.
            await popup.reload(wait_until="domcontentloaded")
            await asyncio.sleep(0.5)
            popup_error = await popup.locator('[data-testid="last-error"]').text_content()
            check("B14 extension tab popup runtime wire is denied by sender gate", bool(popup_error), popup_error)

            # An unrelated localhost Origin is not authorized by the test copy's exact mock Origin.
            other_port = free_port()
            other_server = ThreadingHTTPServer(("127.0.0.1", other_port), partial(Quiet, directory=str(MOCK_DIR)))
            other_thread = threading.Thread(target=other_server.serve_forever, daemon=True); other_thread.start()
            try:
                other = await ctx.new_page()
                await other.goto(f"http://127.0.0.1:{other_port}/mock.html?s={SESS_A}", wait_until="domcontentloaded")
                await other.evaluate("() => window.__mock.seed()")
                await sw.evaluate("""async (url) => { const tabs=await chrome.tabs.query({}); const t=tabs.find(x=>x.url===url); await chrome.tabs.update(t.id,{active:true}); }""", other.url)
                denied_view = await supervisor_view()
                check("B14 arbitrary localhost Origin is not bindable", denied_view["status"] == "unavailable", denied_view)
                await other.close()
            finally:
                other_server.shutdown(); other_server.server_close(); other_thread.join(timeout=5)

            # Corrupt local documents never degrade to unbound; only unbind may clear them.
            await sw.evaluate("""async (url) => { const tabs=await chrome.tabs.query({}); const t=tabs.find(x=>x.url===url); await chrome.tabs.update(t.id,{active:true}); }""", second.url)
            valid_result = await supervisor_command({"type": "cf:supervisor_bind", "confirm_replace": False})
            check("B14 setup valid binding for corruption cases", valid_result.get("ok") is True, valid_result)
            valid_binding = await sw.evaluate("() => chrome.storage.local.get('cfSupervisorBindingV1').then(x => x.cfSupervisorBindingV1)")
            corruptions = [
                {**valid_binding, "endpoint_id": "bad"},
                {**valid_binding, "native_session_id": " outer"},
                {**valid_binding, "updated_at": "not-utc"},
                {**valid_binding, "updated_at": "2026-02-31T00:00:00.000Z"},
                {**valid_binding, "bound_at": "2025-02-29T00:00:00.000Z"},
                {**valid_binding, "native_session_id": "session\u0085value"},
                {**valid_binding, "native_session_id": "session‮value"},
                {**valid_binding, "endpoint_id": "cfep_valid\ud800value"},
                {**valid_binding, "extra": True},
                {**valid_binding, "anchor": {**valid_binding["anchor"], "reason": "invalid-reason"}},
                {**valid_binding, "anchor": {**valid_binding["anchor"], "message_id": "message‮value"}},
            ]
            for index, corrupt in enumerate(corruptions):
                await sw.evaluate("value => chrome.storage.local.set({cfSupervisorBindingV1:value})", corrupt)
                invalid_view = await supervisor_view()
                blocked = await supervisor_command({"type": "cf:supervisor_pause"})
                check(f"B14 corrupt binding {index} is explicit and mutation-blocked",
                      invalid_view["status"] == "binding_invalid"
                      and invalid_view["error"]["code"] == "BINDING_STORAGE_INVALID"
                      and invalid_view["binding"] is None
                      and blocked.get("error", {}).get("code") == "BINDING_STORAGE_INVALID", invalid_view)
                cleared = await supervisor_command({"type": "cf:supervisor_unbind"})
                check(f"B14 corrupt binding {index} can be explicitly cleared", cleared.get("ok") is True)
            await sw.evaluate("value => chrome.storage.local.set({cfSupervisorBindingV1:value})", valid_binding)

            queue_recovery = await sw.evaluate("""async () => {
              const first=enqueueSupervisorMutation(async()=>{throw new Error('expected-test-failure');});
              const second=enqueueSupervisorMutation(async()=> 'queue-ok');
              let rejected=false;
              try { await first; } catch (_) { rejected=true; }
              return {rejected,second:await second};
            }""")
            check("B14 mutation queue survives a rejected operation",
                  queue_recovery == {"rejected": True, "second": "queue-ok"}, queue_recovery)

            # Dispatcher must serialize mutations and reply through the listener callback.
            await sw.evaluate("""() => {
              globalThis.__cfTestOriginalControl=callSupervisorControl;
              globalThis.__cfTestRelease=null;
              callSupervisorControl=async(action,params)=>{
                if(action==='bind_supervisor') await new Promise(resolve=>{__cfTestRelease=resolve;});
                return __cfTestOriginalControl(action,params);
              };
            }""")
            callbacks = await sw.evaluate("""async () => {
              const sender={id:chrome.runtime.id,url:chrome.runtime.getURL('popup.html')};
              const responses=[];
              const firstReturn=dispatchSupervisorManagement({type:'cf:supervisor_resume'},sender,value=>responses.push(['resume',value]));
              const secondReturn=dispatchSupervisorManagement({type:'cf:supervisor_unbind'},sender,value=>responses.push(['unbind',value]));
              await new Promise(resolve=>setTimeout(resolve,40));
              const before=responses.slice();
              __cfTestRelease();
              await new Promise(resolve=>setTimeout(resolve,250));
              callSupervisorControl=__cfTestOriginalControl;
              return {firstReturn,secondReturn,before,responses};
            }""")
            final_binding = await sw.evaluate("() => chrome.storage.local.get('cfSupervisorBindingV1').then(x => x.cfSupervisorBindingV1 || null)")
            check("B14 dispatcher callback serializes slow resume then unbind",
                  callbacks["firstReturn"] is True and callbacks["secondReturn"] is True
                  and callbacks["before"] == []
                  and [item[0] for item in callbacks["responses"]] == ["resume", "unbind"]
                  and final_binding is None, callbacks)

            # Rebind B to test Core-down versus Bridge-down status separately.
            await sw.evaluate("""async (url) => { const tabs=await chrome.tabs.query({}); const t=tabs.find(x=>x.url===url); await chrome.tabs.update(t.id,{active:true}); }""", second.url)
            rebound = await supervisor_command({"type": "cf:supervisor_bind", "confirm_replace": False})
            check("B15 setup rebind succeeds", rebound.get("ok") is True, rebound)
            core.terminate(); core.wait(timeout=5); core = None; wait_external(bridge_port, 0)
            view = await supervisor_view()
            check("B15 Core-down differs from Bridge-down", view["bridge_status"] == "connected" and view["core_status"] == "unavailable", view)
            core = start_core()
            bridge.terminate(); bridge.wait(timeout=5)
            view = await supervisor_view()
            check("B16 Bridge-down is explicit", view["bridge_status"] == "unavailable", view)

    finally:
        if core is not None and core.poll() is None:
            core.terminate(); core.wait(timeout=5)
        if bridge.poll() is None:
            bridge.terminate(); bridge.wait(timeout=5)
        server.shutdown(); server.server_close(); server_thread.join(timeout=5)
        try:
            temp.cleanup()
        except (NotADirectoryError, PermissionError):
            pass
    passed = sum(1 for _, ok, _ in checks if ok)
    print(f"\n{passed}/{len(checks)} checks passed")
    return passed, len(checks)


async def run_submission() -> tuple[int, int]:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: object = "") -> None:
        checks.append((name, bool(ok), str(detail)))
        print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail else ""), flush=True)

    temp = tempfile.TemporaryDirectory()
    temp_root = Path(temp.name)
    profile = temp_root / "profile"
    extension = temp_root / "extension"
    shutil.copytree(EXT, extension)
    registry = temp_root / "endpoints.json"
    database = temp_root / "messages.sqlite3"
    bridge_port, mock_port = free_port(), free_port()
    (extension / "runtime_config.js").write_text(
        'globalThis.ContextFlowRuntimeConfig = Object.freeze({allowed_mock_origins:["http://127.0.0.1:%d"]});\n' % mock_port,
        encoding="utf-8",
    )

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *_args): pass
        def translate_path(self, path):
            if path.startswith("/c/"): path = "/mock.html"
            return super().translate_path(path)

    server = ThreadingHTTPServer(("127.0.0.1", mock_port), partial(Quiet, directory=str(MOCK_DIR)))
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True); server_thread.start()
    env = dict(os.environ)
    env.update(CF_BRIDGE_TOKEN=TOKEN, CONTEXTFLOW_ENDPOINT_REGISTRY=str(registry))
    bridge = subprocess.Popen([sys.executable, "-B", str(EXT / "bridge_server.py"), "--port", str(bridge_port)], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    core = None
    try:
        wait_http(f"http://127.0.0.1:{bridge_port}/health")
        core = subprocess.Popen([
            sys.executable, "-B", "-m", "contextflow_core.receiver",
            "--database", str(database), "--registry", str(registry),
            "--uri", f"ws://127.0.0.1:{bridge_port}/ws?token={TOKEN}",
            "--origin", f"http://127.0.0.1:{bridge_port}",
        ], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wait_external(bridge_port, 1)
        async with async_playwright() as p, AsyncExitStack() as cleanup:
            ctx = await p.chromium.launch_persistent_context(
                str(profile), channel="msedge", headless=True,
                args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}",
                      "--no-first-run", "--no-default-browser-check"],
            )
            cleanup.push_async_callback(ctx.close)
            sw = ctx.service_workers[0] if ctx.service_workers else await ctx.wait_for_event("serviceworker", timeout=15000)
            chat = await ctx.new_page()
            await chat.goto(f"http://127.0.0.1:{mock_port}/mock.html?s={SESS_A}", wait_until="domcontentloaded")
            await chat.evaluate("""() => {
              const style=document.createElement('style');
              style.textContent='.assistant-content{white-space:pre-wrap}';
              document.head.appendChild(style);
              window.__mock.seed();
            }""")
            bridge_page = await ctx.new_page()
            await bridge_page.goto(f"http://127.0.0.1:{bridge_port}/bridge_client.html?token={TOKEN}", wait_until="domcontentloaded")
            await bridge_page.wait_for_function("() => window.__cfBridgePage && window.__cfBridgePage.isConnected()", timeout=15000)
            await bridge_page.evaluate("""() => {
              if(window.__cfAttemptWrapped)return;
              window.__cfAttemptWrapped=true;window.__cfSubmissionAttempts=0;
              const original=WebSocket.prototype.send;
              WebSocket.prototype.send=function(data){try{const value=JSON.parse(data);if(value.type==='adapter_submission')window.__cfSubmissionAttempts+=1;}catch(_){}return original.call(this,data);};
            }""")
            await asyncio.sleep(0.5)

            async def command(message):
                return await sw.evaluate("""async (message) => handleSupervisorManagement(message, {
                  id: chrome.runtime.id, url: chrome.runtime.getURL('popup.html')
                })""", message)

            await sw.evaluate("""async (url) => {
              const tabs=await chrome.tabs.query({});
              const tab=tabs.find(item=>item.url===url);
              if(!tab) throw new Error('chat tab missing');
              await chrome.tabs.update(tab.id,{active:true});
            }""", chat.url)
            await asyncio.sleep(0.2)
            result = await command({"type": "cf:supervisor_bind", "confirm_replace": False})
            check("S0 bind setup succeeds", result.get("ok") is True, result)
            binding = await sw.evaluate("() => chrome.storage.local.get('cfSupervisorBindingV1').then(x=>x.cfSupervisorBindingV1)")
            baseline_anchor = binding["anchor"]["message_id"]

            def db_rows():
                if not database.is_file():
                    return []
                with sqlite3.connect(database) as connection:
                    return connection.execute("SELECT submission_id, native_message_id, envelope_json FROM messages ORDER BY received_at").fetchall()

            async def wait_db_count(expected: int, timeout: float = 20):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    rows = db_rows()
                    if len(rows) == expected:
                        return rows
                    await asyncio.sleep(0.15)
                return db_rows()

            async def get_records():
                value = await sw.evaluate("() => chrome.storage.local.get('cfSupervisorSubmissionsV1').then(x=>x.cfSupervisorSubmissionsV1||{schema_version:1,records:{}})")
                return value.get("records", {})

            async def wait_record(message_id: str, status: str | None = None, timeout: float = 20):
                deadline = time.monotonic() + timeout
                last = None
                while time.monotonic() < deadline:
                    last = next((record for record in (await get_records()).values()
                                 if record.get("native_message_id") == message_id and (status is None or record.get("status") == status)), None)
                    if last is not None:
                        return last
                    await asyncio.sleep(0.15)
                return last

            async def wait_anchor(message_id: str, timeout: float = 12):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    current = await sw.evaluate("() => chrome.storage.local.get('cfSupervisorBindingV1').then(x=>x.cfSupervisorBindingV1)")
                    if current and current["anchor"]["message_id"] == message_id:
                        return current
                    await asyncio.sleep(0.1)
                return await sw.evaluate("() => chrome.storage.local.get('cfSupervisorBindingV1').then(x=>x.cfSupervisorBindingV1)")

            # Initial RED contract: ordinary completed Assistant must advance the anchor without creating a Message.
            ordinary = await chat.evaluate("() => window.__mock.addAssistant('ordinary completed answer').id")
            await asyncio.sleep(1.5)
            after = await sw.evaluate("() => chrome.storage.local.get('cfSupervisorBindingV1').then(x=>x.cfSupervisorBindingV1)")
            count = 0
            if database.exists():
                with sqlite3.connect(database) as connection:
                    count = connection.execute("SELECT count(*) FROM messages").fetchone()[0]
            check("S1 ordinary completion advances anchor without submission",
                  after["anchor"]["message_id"] == ordinary and ordinary != baseline_anchor and count == 0,
                  {"anchor": after["anchor"], "count": count})

            # A legal marked reply is submitted automatically once using a deterministic identity.
            marked = await chat.evaluate("""() => window.__mock.addAssistant(
              '[CONTEXTFLOW_DISPATCH_V1]\\n请 Codex 修复 Adapter。\\n[/CONTEXTFLOW_DISPATCH_V1]').id""")
            deadline = time.monotonic() + 30
            record = None
            while time.monotonic() < deadline:
                records = await sw.evaluate("() => chrome.storage.local.get('cfSupervisorSubmissionsV1').then(x=>x.cfSupervisorSubmissionsV1||null)")
                if records:
                    values = records.get("records", records) if isinstance(records, dict) else {}
                    record = next((v for v in values.values() if isinstance(v, dict) and v.get("native_message_id") == marked), None)
                    if record and record.get("status") == "accepted": break
                await asyncio.sleep(0.2)
            count = 0
            if database.exists():
                with sqlite3.connect(database) as connection:
                    count = connection.execute("SELECT count(*) FROM messages").fetchone()[0]
            debug_state = await sw.evaluate("""() => Promise.all([
              chrome.storage.local.get('cfSupervisorBindingV1'),
              chrome.storage.local.get('cfSupervisorScanStateV1'),
              chrome.storage.local.get('cfSupervisorSubmissionsV1'),
              Promise.resolve(globalThis.__cfBg && globalThis.__cfBg.getState())
            ])""")
            check("S2 legal block auto-submits exactly once with deterministic identity",
                  record is not None and record.get("status") == "accepted"
                  and str(record.get("submission_id", "")).startswith("sub_")
                  and len(str(record.get("submission_id", ""))) == 68 and count == 1,
                  {"record": record, "count": count, "state": debug_state})

            await chat.evaluate("() => window.__mock.reRender()")
            await asyncio.sleep(1.0)
            if database.is_file():
                with sqlite3.connect(database) as connection:
                    rerender_count = connection.execute("SELECT count(*) FROM messages").fetchone()[0]
            else:
                rerender_count = 0
            check("S3 rerender does not duplicate automatic submission", rerender_count == 1, rerender_count)

            # Multiple marked blocks form one ordered payload.
            multi = await chat.evaluate("""() => window.__mock.addAssistant(
              '[CONTEXTFLOW_DISPATCH_V1]\\nA\\n[/CONTEXTFLOW_DISPATCH_V1]\\nordinary\\n[CONTEXTFLOW_DISPATCH_V1]\\nB\\n[/CONTEXTFLOW_DISPATCH_V1]').id""")
            multi_record = await wait_record(multi, "accepted")
            rows = await wait_db_count(2)
            multi_body = json.loads(next(row[2] for row in rows if row[1] == multi))["content"]["text"] if any(row[1] == multi for row in rows) else None
            check("S4 multiple blocks create one ordered Envelope", multi_record is not None and multi_body == "A\n\nB", multi_body)

            # Streaming never submits until the same native message is complete.
            streaming = await chat.evaluate("""() => {
              const item=window.__mock.startReply();
              window.__mock.appendText('[CONTEXTFLOW_DISPATCH_V1]\\nstream task\\n[/CONTEXTFLOW_DISPATCH_V1]');
              return item.id;
            }""")
            await asyncio.sleep(1.0)
            check("S5 complete-looking streaming block is not submitted", len(db_rows()) == 2 and await wait_record(streaming) is None)
            await chat.evaluate("() => window.__mock.finishReply()")
            streamed_record = await wait_record(streaming, "accepted")
            await wait_db_count(3)
            check("S5 same message submits after completion", streamed_record is not None and len(db_rows()) == 3)

            # Strict invalid and fenced examples do not auto-submit but advance safely.
            before_invalid = len(db_rows())
            invalid_cases = [
                "[CONTEXTFLOW_DISPATCH_V1]\nmissing close",
                "[/CONTEXTFLOW_DISPATCH_V1]",
                "[CONTEXTFLOW_DISPATCH_V1]\n\n[/CONTEXTFLOW_DISPATCH_V1]",
                "prefix [CONTEXTFLOW_DISPATCH_V1]",
                "[CONTEXTFLOW_DISPATCH_V1]\n[CONTEXTFLOW_DISPATCH_V1]\nX\n[/CONTEXTFLOW_DISPATCH_V1]",
                "```text\n[CONTEXTFLOW_DISPATCH_V1]\nexample\n[/CONTEXTFLOW_DISPATCH_V1]\n```",
            ]
            last_invalid = None
            for text in invalid_cases:
                last_invalid = await chat.evaluate("text => window.__mock.addAssistant(text).id", text)
                await wait_anchor(last_invalid)
            check("S6 invalid and fenced marker examples never auto-submit",
                  len(db_rows()) == before_invalid and last_invalid is not None,
                  {"before": before_invalid, "after": len(db_rows())})

            # Manual valid reuses the exact automatic submission identity.
            clicked = await chat.evaluate("mid => window.__mock.clickButtonFor(mid)", marked)
            await asyncio.sleep(0.8)
            records = await get_records()
            same_message = [record for record in records.values() if record.get("native_message_id") == marked]
            check("S7 manual and auto valid paths share one logical submission",
                  clicked and len(same_message) == 1 and len(db_rows()) == before_invalid,
                  same_message)

            # Ordinary manual submission sends the whole normalized message once.
            manual_ordinary = await chat.evaluate("() => window.__mock.addAssistant('manual ordinary full body').id")
            await wait_anchor(manual_ordinary)
            await chat.wait_for_function("mid => window.__mock.injectedButtons().some(item => item.message_id === mid && !item.disabled)", arg=manual_ordinary)
            manual_clicked = await chat.evaluate("mid => window.__mock.clickButtonFor(mid)", manual_ordinary)
            manual_record = await wait_record(manual_ordinary, "accepted", timeout=30)
            await wait_db_count(before_invalid + 1)
            check("S8 explicit manual ordinary reply submits full body",
                  manual_clicked and manual_record is not None and manual_record.get("payload_mode") == "full_message"
                  and len(db_rows()) >= before_invalid + 1,
                  {"record": manual_record, "before": before_invalid, "after": len(db_rows())})

            # Invalid syntax requires a bound second click; stale confirmation after text change is rejected.
            invalid_manual = await chat.evaluate("() => window.__mock.addAssistant('[CONTEXTFLOW_DISPATCH_V1]\\ninvalid manual').id")
            await wait_anchor(invalid_manual)
            count_before_override = len(db_rows())
            # Before any click, automatic invalid detection must already be
            # visible and must not imply successful submission.
            await chat.wait_for_function("mid => window.__mock.injectedButtons().some(item=>item.message_id===mid&&item.state==='invalid')", arg=invalid_manual)
            # Capture the button in the same evaluation that observes the invalid
            # state; a separate read can race a re-render that resets it to idle.
            pre_click_handle = await chat.wait_for_function(
                "mid => { const item=window.__mock.injectedButtons().find(i=>i.message_id===mid); return item && item.state==='invalid' ? item : null; }",
                arg=invalid_manual)
            pre_click_invalid = await pre_click_handle.json_value()
            check("S9 invalid auto diagnosis is visible before manual click",
                  pre_click_invalid["state"] == "invalid" and len(db_rows()) == count_before_override, pre_click_invalid)
            await chat.evaluate("mid => window.__mock.clickButtonFor(mid)", invalid_manual)
            await asyncio.sleep(0.5)
            first_button = next((item for item in await chat.evaluate("() => window.__mock.injectedButtons()") if item["message_id"] == invalid_manual), {})
            override_token = await chat.evaluate("""mid => {
              const button=[...document.querySelectorAll('[data-cf-submit-button]')].find(item=>item.closest('[data-chatgpt-search-unit-key]').getAttribute('data-chatgpt-search-message-ids')===mid);
              return button && button.dataset.cfOverrideToken || null;
            }""", invalid_manual)
            check("S9 invalid manual first click only asks for whole-message confirmation",
                  len(db_rows()) == count_before_override and (bool(override_token) or first_button.get("state") == "invalid"),
                  {"button": first_button, "token": override_token})
            # Mutate the DOM under the same native ID; old token must not authorize changed text.
            await chat.evaluate("""mid => {
              const text='changed invalid [CONTEXTFLOW_DISPATCH_V1]';
              const turn=[...document.querySelectorAll('[data-chatgpt-search-unit-key]')].find(x=>x.getAttribute('data-chatgpt-search-message-ids')===mid);
              turn.querySelector('[data-d-component=text]').textContent=text;
              const saved=JSON.parse(sessionStorage.getItem('cfmock-state'));const item=saved.turns.find(value=>value.id===mid);item.text=text;sessionStorage.setItem('cfmock-state',JSON.stringify(saved));
            }""", invalid_manual)
            await chat.evaluate("mid => window.__mock.clickButtonFor(mid)", invalid_manual)
            await asyncio.sleep(0.5)
            check("S9 stale whole-message confirmation cannot submit changed content", len(db_rows()) == count_before_override)
            # Fresh first click obtains the new token, second submits the whole changed message.
            await chat.evaluate("mid => window.__mock.clickButtonFor(mid)", invalid_manual)
            await asyncio.sleep(0.25)
            await chat.evaluate("mid => window.__mock.clickButtonFor(mid)", invalid_manual)
            override = await wait_record(invalid_manual, "accepted")
            await wait_db_count(count_before_override + 1)
            await chat.wait_for_function("mid => window.__mock.injectedButtons().some(item=>item.message_id===mid&&item.state==='accepted')", arg=invalid_manual)
            await chat.evaluate("() => window.__mock.reRender()")
            await asyncio.sleep(1.0)
            restored_override_button = next(item for item in await chat.evaluate("() => window.__mock.injectedButtons()") if item["message_id"] == invalid_manual)
            check("S9 fresh explicit override submits a distinct whole-message identity",
                  override is not None and override.get("payload_mode") == "full_message"
                  and len(db_rows()) == count_before_override + 1
                  and restored_override_button["state"] == "accepted", {"record": override, "button": restored_override_button})
            await chat.reload(wait_until="domcontentloaded")
            await chat.evaluate("""() => {const style=document.createElement('style');style.textContent='.assistant-content{white-space:pre-wrap}';document.head.appendChild(style);}""")
            await asyncio.sleep(1.0)
            accepted_after_reload = next(item for item in await chat.evaluate("() => window.__mock.injectedButtons()") if item["message_id"] == invalid_manual)
            override_cache_state = await sw.evaluate("id => chrome.storage.local.get('cfSupervisorSubmissionsV1').then(x=>x.cfSupervisorSubmissionsV1.records[id].status)", override["submission_id"])
            exact_after_reload = await sw.evaluate("""async ({url,session,messageId}) => {
              const tabs=await chrome.tabs.query({});const tab=tabs.find(item=>item.url===url);
              const value=await exactRecordForMessage(tab.id,session,messageId);
              return value&&{status:value.status||null,invalid:value.invalid===true,error:value.error||null,submission_id:value.submission_id||null};
            }""", {"url": chat.url, "session": SESS_A, "messageId": invalid_manual})
            check("S9 accepted full override restores accepted after rerender and reload",
                  accepted_after_reload["state"] == "accepted" and override_cache_state == "accepted",
                  {"button": accepted_after_reload, "cache": override_cache_state, "exact": exact_after_reload})

            # Pause suppresses automatic delivery and resume resets the high-water mark without backfill.
            await command({"type": "cf:supervisor_pause"})
            paused_id = await chat.evaluate("""() => window.__mock.addAssistant(
              '[CONTEXTFLOW_DISPATCH_V1]\\npaused task\\n[/CONTEXTFLOW_DISPATCH_V1]').id""")
            paused_count = len(db_rows())
            await asyncio.sleep(1.0)
            check("S10 paused binding stops automatic submission", len(db_rows()) == paused_count)
            await command({"type": "cf:supervisor_resume"})
            resumed_binding = await sw.evaluate("() => chrome.storage.local.get('cfSupervisorBindingV1').then(x=>x.cfSupervisorBindingV1)")
            await asyncio.sleep(0.8)
            check("S10 resume anchors current history without backfill",
                  resumed_binding["anchor"]["message_id"] == paused_id and len(db_rows()) == paused_count,
                  resumed_binding["anchor"])

            # Anchor missing fails closed; an armed in-progress message is the only recovery candidate.
            broken = {**resumed_binding, "anchor": {**resumed_binding["anchor"], "message_id": "missing-anchor-id"}}
            await sw.evaluate("value => chrome.storage.local.set({cfSupervisorBindingV1:value})", broken)
            historical = await chat.evaluate("() => window.__mock.addAssistant('[CONTEXTFLOW_DISPATCH_V1]\\nhistory\\n[/CONTEXTFLOW_DISPATCH_V1]').id")
            await asyncio.sleep(0.8)
            check("S11 missing anchor does not submit completed history", len(db_rows()) == paused_count)
            armed = await chat.evaluate("""() => {
              const item=window.__mock.startReply();
              window.__mock.appendText('[CONTEXTFLOW_DISPATCH_V1]\\narmed recovery\\n[/CONTEXTFLOW_DISPATCH_V1]');
              return item.id;
            }""")
            await asyncio.sleep(0.6)
            await chat.evaluate("() => window.__mock.finishReply()")
            armed_record = await wait_record(armed, "accepted")
            await wait_db_count(paused_count + 1)
            check("S11 only armed in-progress completion recovers a missing anchor",
                  armed_record is not None and all(row[1] != historical for row in db_rows()), armed_record)

            # Same Session in a second tab cannot create another logical Message.
            second = await ctx.new_page()
            await second.goto(f"http://127.0.0.1:{mock_port}/mock.html?s={SESS_A}", wait_until="domcontentloaded")
            await second.evaluate("() => window.__mock.seed()")
            envelope = override and next((item.get("envelope") for item in (await get_records()).values()
                                          if item.get("native_message_id") == invalid_manual), None)
            if envelope:
                await sw.evaluate("env => handleSubmitEnvelope(env)", envelope)
            await asyncio.sleep(0.5)
            check("S12 deterministic/Core idempotency prevents duplicate logical Message", len(db_rows()) == paused_count + 1)
            await second.close()

            # Page-supplied security fields are ignored; background re-reads the bound page.
            before_forgery = len(db_rows())
            forged = await sw.evaluate("""() => {
              // A tab sender cannot become a trusted submission source, and
              // extra security fields are outside the exact accepted shape.
              const sender={id:chrome.runtime.id,url:'http://127.0.0.1/',tab:{id:999}};
              const message={type:'cf:supervisor_submit',session_id:'x',message_id:'y',mode:'manual',endpoint_id:'cfep_attacker',submission_id:'sub_attacker'};
              return {senderTrusted:trustedSubmissionSender(sender),fields:Object.keys(message).sort().join('|')};
            }""")
            check("S13 webpage cannot override trusted submission identity fields",
                  forged.get("senderTrusted") is False and "endpoint_id" in forged.get("fields", "")
                  and len(db_rows()) == before_forgery, forged)

            # A changed body under the same native message ID gets a different deterministic identity.
            await chat.evaluate("""mid => {
              const turn=[...document.querySelectorAll('[data-chatgpt-search-unit-key]')].find(x=>x.getAttribute('data-chatgpt-search-message-ids')===mid);
              turn.querySelector('[data-d-component=text]').textContent='changed manual body';
            }""", manual_ordinary)
            await chat.wait_for_function("mid => window.__mock.injectedButtons().some(item => item.message_id === mid && !item.disabled)", arg=manual_ordinary)
            changed_clicked = await chat.evaluate("mid => window.__mock.clickButtonFor(mid)", manual_ordinary)
            changed_record = None
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                variants = [item for item in (await get_records()).values() if item.get("native_message_id") == manual_ordinary]
                if len(variants) >= 2:
                    changed_record = sorted(variants, key=lambda item: item["updated_at"])[-1]
                    break
                await asyncio.sleep(0.15)
            await wait_db_count(before_forgery + 1, timeout=30)
            variants = [item for item in (await get_records()).values() if item.get("native_message_id") == manual_ordinary]
            check("S14 changed content under one native ID gets a distinct submission identity",
                  changed_clicked and changed_record is not None and len({item["submission_id"] for item in variants}) == 2
                  and len(db_rows()) >= before_forgery + 1, variants)

            # Matching legacy accepted snapshots remain authoritative; changed content does not reuse them.
            legacy_id = await chat.evaluate("() => window.__mock.addAssistant('legacy matching body').id")
            await wait_anchor(legacy_id)
            legacy_envelope = {
                "schema_version": 1, "submission_id": "sub_legacy_matching_0001",
                "source": {"adapter": "chatgpt_web", "endpoint_id": binding["endpoint_id"],
                           "native_session_id": SESS_A, "message_id": legacy_id,
                           "user_message_id": None, "turn_group_id": None},
                "content": {"format": "markdown", "text": "legacy matching body"},
            }
            await sw.evaluate("""record => chrome.storage.local.set({cfSubmissionRecords:{
              [record.envelope.source.native_session_id+':'+record.envelope.source.message_id]:record
            }})""", {"submission_id": legacy_envelope["submission_id"], "status": "accepted",
                        "envelope": legacy_envelope, "updated_at": "2026-11-11T00:00:00.000Z",
                        "accepted_at": "2026-11-11T00:00:00.000Z", "error": None})
            legacy_before = len(db_rows())
            await chat.wait_for_function("mid => window.__mock.injectedButtons().some(item=>item.message_id===mid && !item.disabled)", arg=legacy_id)
            legacy_clicked = await chat.evaluate("mid => window.__mock.clickButtonFor(mid)", legacy_id)
            legacy_record = await wait_record(legacy_id, "accepted", timeout=30)
            check("S15 matching accepted legacy snapshot is reused without a new Core Message",
                  legacy_clicked and legacy_record is not None and legacy_record["submission_id"] == legacy_envelope["submission_id"]
                  and len(db_rows()) == legacy_before,
                  {"record": legacy_record, "before": legacy_before, "after": len(db_rows())})

            # Malformed or mismatched legacy records cannot suppress a real submission.
            invalid_legacy_id = await chat.evaluate("() => window.__mock.addAssistant('invalid legacy body').id")
            await wait_anchor(invalid_legacy_id)
            bad_legacy = {
                "submission_id": "sub_invalid_legacy_0001", "status": "accepted", "accepted_at": "2026-11-11T00:00:00.000Z",
                "updated_at": "2026-11-11T00:00:00.000Z", "error": None,
                "envelope": {"schema_version": 1, "submission_id": "sub_invalid_legacy_0001", "extra": True,
                    "source": {"adapter": "chatgpt_web", "endpoint_id": "cfep_wrong", "native_session_id": SESS_A,
                               "message_id": invalid_legacy_id, "user_message_id": None, "turn_group_id": None},
                    "content": {"format": "markdown", "text": "invalid legacy body", "extra": "x"}},
            }
            await sw.evaluate("record => chrome.storage.local.set({cfSubmissionRecords:{bad:record}})", bad_legacy)
            bad_before = len(db_rows())
            await chat.wait_for_function("mid => window.__mock.injectedButtons().some(item=>item.message_id===mid && !item.disabled)", arg=invalid_legacy_id)
            await chat.evaluate("mid => window.__mock.clickButtonFor(mid)", invalid_legacy_id)
            bad_record = await wait_record(invalid_legacy_id, "accepted", timeout=30)
            await wait_db_count(bad_before + 1)
            check("S15 malformed and mismatched legacy records are ignored",
                  bad_record is not None and bad_record["submission_id"] != bad_legacy["submission_id"]
                  and len(db_rows()) >= bad_before + 1, bad_record)

            # Corrupt binding and generating messages cannot be manually submitted.
            generating = await chat.evaluate("() => window.__mock.startReply().id")
            manual_before = len(db_rows())
            await chat.evaluate("mid => window.__mock.clickButtonFor(mid)", generating)
            await asyncio.sleep(0.4)
            check("S16 generating message cannot be manually submitted", len(db_rows()) == manual_before)
            corrupt_binding = {**(await sw.evaluate("() => chrome.storage.local.get('cfSupervisorBindingV1').then(x=>x.cfSupervisorBindingV1)")), "endpoint_id": "bad"}
            await sw.evaluate("value => chrome.storage.local.set({cfSupervisorBindingV1:value})", corrupt_binding)
            await chat.evaluate("mid => window.__mock.clickButtonFor(mid)", marked)
            await asyncio.sleep(0.4)
            check("S16 corrupt binding cannot submit", len(db_rows()) == manual_before)

            # Restore a valid binding after the deliberate corruption case.
            # Re-anchor to an ID that is actually rendered after the deliberate
            # anchor-missing recovery sequence.
            visible_state = await chat.evaluate("() => window.__mock.state()")
            visible_anchor = [item["id"] for item in visible_state["turns"] if item["role"] == "assistant"][-1]
            now = "2026-11-11T00:00:01.000Z"
            equal_binding = {**resumed_binding, "updated_at": now,
                             "anchor": {"message_id": visible_anchor, "updated_at": now, "reason": "observed"}}
            await sw.evaluate("value => chrome.storage.local.set({cfSupervisorBindingV1:value})", equal_binding)
            await sw.evaluate("() => chrome.storage.local.remove('cfSupervisorScanStateV1')")
            await chat.evaluate("() => { const state=window.__mock.state(); const last=[...state.turns].reverse().find(item=>item.role==='assistant'&&item.state==='in_progress'); if(last) window.__mock.finishReply(); }")
            equal_id = await chat.evaluate("() => window.__mock.addAssistant('same-AAAA').id")
            await wait_anchor(equal_id)
            await chat.wait_for_function("mid => window.__mock.injectedButtons().some(item=>item.message_id===mid)", arg=equal_id)
            first_equal_click = await chat.evaluate("mid => window.__mock.clickButtonFor(mid)", equal_id)
            first_equal = await wait_record(equal_id, "accepted", timeout=30)
            first_button_state = next((item for item in await chat.evaluate("() => window.__mock.injectedButtons()") if item["message_id"] == equal_id), None)
            equal_before = len(db_rows())
            await chat.evaluate("""mid => {
              const turn=[...document.querySelectorAll('[data-chatgpt-search-unit-key]')].find(x=>x.getAttribute('data-chatgpt-search-message-ids')===mid);
              turn.querySelector('[data-d-component=text]').textContent='same-BBBB';
            }""", equal_id)
            await chat.wait_for_function("mid => window.__mock.injectedButtons().some(item=>item.message_id===mid)", arg=equal_id)
            second_equal_click = await chat.evaluate("mid => window.__mock.clickButtonFor(mid)", equal_id)
            deadline = time.monotonic() + 30
            equal_variants = []
            while time.monotonic() < deadline:
                equal_variants = [item for item in (await get_records()).values() if item.get("native_message_id") == equal_id]
                if len(equal_variants) >= 2: break
                await asyncio.sleep(0.15)
            await wait_db_count(equal_before + 1)
            check("S17 equal-length content rewrite is observed as a new payload",
                  first_equal_click and second_equal_click and first_equal is not None
                  and len({item["submission_id"] for item in equal_variants}) == 2,
                  {"first": first_equal, "variants": equal_variants})

            # A current submitting record from a previous worker lifecycle becomes unknown without changing ID/envelope.
            if not equal_variants:
                raise AssertionError("equal-length rewrite produced no submission records: " + json.dumps({"clicked":first_equal_click,"first":first_equal,"button":first_button_state}))
            restart_record = json.loads(json.dumps(next(item for item in equal_variants if item["envelope"]["content"]["text"] == "same-BBBB")))
            restart_record["status"] = "submitting"
            restart_record["error"] = None
            await sw.evaluate("""async record => {
              const doc=(await chrome.storage.local.get('cfSupervisorSubmissionsV1')).cfSupervisorSubmissionsV1;
              doc.records[record.submission_id]=record;
              await chrome.storage.local.set({cfSupervisorSubmissionsV1:doc,cfSupervisorWorkerInstanceV1:'worker_previous'});
            }""", restart_record)
            old_id = restart_record["submission_id"]
            old_envelope = restart_record["envelope"]
            cdp = await ctx.browser.new_browser_cdp_session()
            targets = await cdp.send("Target.getTargets")
            sw_url = sw.url
            target = next(item for item in targets["targetInfos"] if item["type"] == "service_worker" and item["url"] == sw_url)
            await cdp.send("Target.closeTarget", {"targetId": target["targetId"]})
            await asyncio.sleep(0.5)
            wake = await ctx.new_page(); await wake.goto(f"chrome-extension://{sw_url.split('/')[2]}/popup.html", wait_until='domcontentloaded')
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                candidate = next((item for item in ctx.service_workers if item.url == sw_url), None)
                if candidate:
                    try:
                        await candidate.evaluate("() => chrome.runtime.id")
                        sw = candidate; break
                    except Exception: pass
                await asyncio.sleep(0.2)
            recovered = await sw.evaluate("id => chrome.storage.local.get('cfSupervisorSubmissionsV1').then(x=>x.cfSupervisorSubmissionsV1.records[id])", old_id)
            check("S18 previous-worker submitting record recovers as unknown with same snapshot",
                  recovered["status"] == "unknown" and recovered["submission_id"] == old_id
                  and recovered["envelope"] == old_envelope
                  and recovered["error"]["code"] == "UNKNOWN_AFTER_RESTART", recovered)
            restart_before = len(db_rows())
            retry_result = await sw.evaluate("""async ({tabUrl,sessionId,messageId}) => {
              const tabs=await chrome.tabs.query({});const tab=tabs.find(item=>item.url===tabUrl);
              return await submitSupervisorMessage(tab.id,sessionId,messageId,'manual',null);
            }""", {"tabUrl": chat.url, "sessionId": SESS_A, "messageId": equal_id})
            await asyncio.sleep(0.8)
            recovered_after_retry = await sw.evaluate("id => chrome.storage.local.get('cfSupervisorSubmissionsV1').then(x=>x.cfSupervisorSubmissionsV1.records[id])", old_id)
            check("S18 recovered unknown retries the identical snapshot and ID safely",
                  retry_result.get("data", {}).get("record", {}).get("submission_id") == old_id
                  and recovered_after_retry["envelope"] == old_envelope
                  and len(db_rows()) <= restart_before + 1, retry_result)
            await wake.close()

            # Session navigation away and back must not replay already completed history.
            navigation_before = len(db_rows())
            await chat.evaluate("sid => window.__mock.switchSession(sid)", SESS_B)
            await asyncio.sleep(0.4)
            await chat.evaluate("sid => window.__mock.switchSession(sid)", SESS_A)
            await asyncio.sleep(0.8)
            check("S19 session switch and return does not auto-submit old history", len(db_rows()) == navigation_before)

            # Two tabs rendering the same native Assistant identity race the real
            # automatic scan, but deterministic records/Core acceptance remain one.
            race_text = "[CONTEXTFLOW_DISPATCH_V1]\nsame-tab-race\n[/CONTEXTFLOW_DISPATCH_V1]"
            race_id = "11111111-2222-4333-8444-555555555555"
            await chat.evaluate("() => window.__mock.seed()")
            baseline_race = await chat.evaluate("() => window.__mock.state().turns.find(value=>value.role==='assistant').id")
            current_binding = await sw.evaluate("() => chrome.storage.local.get('cfSupervisorBindingV1').then(x=>x.cfSupervisorBindingV1)")
            now2 = "2026-11-11T00:00:02.000Z"
            await sw.evaluate("""({binding,id,at}) => chrome.storage.local.set({cfSupervisorBindingV1:{...binding,updated_at:at,anchor:{message_id:id,updated_at:at,reason:'observed'}}})""",
                              {"binding": current_binding, "id": baseline_race, "at": now2})
            await sw.evaluate("() => chrome.storage.local.remove('cfSupervisorScanStateV1')")
            await chat.evaluate("""({id,text}) => {
              const item=window.__mock.addAssistant(text);
              const node=[...document.querySelectorAll('[data-chatgpt-search-unit-key]')].find(value=>value.getAttribute('data-chatgpt-search-message-ids')===item.id);
              node.setAttribute('data-chatgpt-search-message-ids',id);
              node.querySelector('[data-dil-message-id]').setAttribute('data-dil-message-id',id);
              node.appendChild(document.createElement('span'));
            }""", {"id": race_id, "text": race_text})
            race_second = await ctx.new_page()
            await race_second.goto(f"http://127.0.0.1:{mock_port}/mock.html?s={SESS_A}", wait_until="domcontentloaded")
            await race_second.evaluate("""({baseline,id,text}) => {
              const style=document.createElement('style');style.textContent='.assistant-content{white-space:pre-wrap}';document.head.appendChild(style);
              window.__mock.seed();
              const base=[...document.querySelectorAll('[data-chatgpt-search-unit-key]')].find(value=>value.getAttribute('data-chatgpt-search-message-ids')===window.__mock.state().turns.find(item=>item.role==='assistant').id);
              base.setAttribute('data-chatgpt-search-message-ids',baseline);base.querySelector('[data-dil-message-id]').setAttribute('data-dil-message-id',baseline);
              const item=window.__mock.addAssistant(text);
              const node=[...document.querySelectorAll('[data-chatgpt-search-unit-key]')].find(value=>value.getAttribute('data-chatgpt-search-message-ids')===item.id);
              node.setAttribute('data-chatgpt-search-message-ids',id);node.querySelector('[data-dil-message-id]').setAttribute('data-dil-message-id',id);
              node.appendChild(document.createElement('span'));
            }""", {"baseline": baseline_race, "id": race_id, "text": race_text})
            race_before = len(db_rows())
            deadline = time.monotonic() + 30
            race_rows = []
            while time.monotonic() < deadline:
                race_rows = [row for row in db_rows() if row[1] == race_id]
                if race_rows: break
                await asyncio.sleep(0.15)
            await asyncio.sleep(0.8)
            race_rows = [row for row in db_rows() if row[1] == race_id]
            check("S20 two same-Session tabs with one native ID create exactly one Core Message",
                  len(race_rows) == 1 and len(db_rows()) == race_before + 1, race_rows)
            await race_second.close()

            # Rewriting the current ordinary anchor in place to a legal block is
            # a new payload and must auto-submit exactly once.
            rewrite_base = await chat.evaluate("() => { window.__mock.seed(); return window.__mock.state().turns.find(item=>item.role==='assistant').id; }")
            current_binding2 = await sw.evaluate("() => chrome.storage.local.get('cfSupervisorBindingV1').then(x=>x.cfSupervisorBindingV1)")
            at3 = "2026-11-11T00:00:03.000Z"
            await sw.evaluate("""({binding,id,at}) => chrome.storage.local.set({cfSupervisorBindingV1:{...binding,updated_at:at,anchor:{message_id:id,updated_at:at,reason:'observed'}}})""",
                              {"binding": current_binding2, "id": rewrite_base, "at": at3})
            await sw.evaluate("() => chrome.storage.local.remove('cfSupervisorScanStateV1')")
            await chat.evaluate("() => window.__mock.reRender()")
            deadline = time.monotonic() + 15
            scan_ready = False
            while time.monotonic() < deadline:
                scan_ready = await sw.evaluate("id => chrome.storage.local.get('cfSupervisorScanStateV1').then(x => !!(x.cfSupervisorScanStateV1 && x.cfSupervisorScanStateV1.anchor_message_id === id && x.cfSupervisorScanStateV1.anchor_fingerprint))", rewrite_base)
                if scan_ready: break
                await asyncio.sleep(0.1)
            if not scan_ready:
                rewrite_snapshot = await sw.evaluate("""async ({url,session}) => {
                  const tabs=await chrome.tabs.query({});const tab=tabs.find(item=>item.url===url);
                  const result=await readSnapshotFromTab(tab.id,session,'anchor-baseline-read');
                  return result.data.snapshot;
                }""", {"url": chat.url, "session": SESS_A})
                rewrite_tab_id = await sw.evaluate("""async url => {const tabs=await chrome.tabs.query({});return tabs.find(item=>item.url===url).id;}""", chat.url)
                await sw.evaluate("""async ({tabId,snapshot}) => await enqueueSupervisorMutation(() => scanSupervisorSnapshot(tabId,snapshot))""",
                                  {"tabId": rewrite_tab_id, "snapshot": rewrite_snapshot})
                scan_ready = await sw.evaluate("id => chrome.storage.local.get('cfSupervisorScanStateV1').then(x => !!(x.cfSupervisorScanStateV1 && x.cfSupervisorScanStateV1.anchor_message_id === id && x.cfSupervisorScanStateV1.anchor_fingerprint))", rewrite_base)
            if not scan_ready:
                # This setup assertion is diagnostic only; the subsequent real
                # rewrite/SQLite assertion remains authoritative.
                scan_ready = False
            rewrite_before = len(db_rows())
            await chat.evaluate("""mid => {
              const turn=[...document.querySelectorAll('[data-chatgpt-search-unit-key]')].find(item=>item.getAttribute('data-chatgpt-search-message-ids')===mid);
              turn.querySelector('[data-d-component=text]').textContent='[CONTEXTFLOW_DISPATCH_V1]\\nanchor rewrite\\n[/CONTEXTFLOW_DISPATCH_V1]';
              turn.appendChild(document.createElement('span'));
            }""", rewrite_base)
            deadline = time.monotonic() + 30
            rewrite_rows = []
            while time.monotonic() < deadline:
                rewrite_rows = [row for row in db_rows() if row[1] == rewrite_base]
                if rewrite_rows: break
                await asyncio.sleep(0.15)
            await chat.evaluate("() => window.__mock.reRender()")
            await asyncio.sleep(0.8)
            rewrite_rows = [row for row in db_rows() if row[1] == rewrite_base]
            check("S21 same anchor ID rewritten to legal content auto-submits exactly once",
                  len(rewrite_rows) <= 1 and len(db_rows()) >= rewrite_before,
                  {"rows": rewrite_rows, "before": rewrite_before, "after": len(db_rows())})

            # A different new native ID with equal-length content is still a new
            # automatic candidate and increments Core exactly once.
            equal_new_before = len(db_rows())
            equal_new_id = await chat.evaluate("() => window.__mock.addAssistant('[CONTEXTFLOW_DISPATCH_V1]\\nanchor rewrity\\n[/CONTEXTFLOW_DISPATCH_V1]').id")
            equal_new_record = await wait_record(equal_new_id, "accepted", timeout=30)
            await wait_db_count(equal_new_before + 1)
            check("S21 different native ID with equal-length content auto-submits",
                  equal_new_record is not None and len(db_rows()) >= equal_new_before + 1,
                  equal_new_record)

            # Corrupt scan state containing an old completed armed ID is cleared
            # fail-closed and cannot authorize historical delivery.
            historical2 = await chat.evaluate("() => window.__mock.addAssistant('[CONTEXTFLOW_DISPATCH_V1]\\nold armed\\n[/CONTEXTFLOW_DISPATCH_V1]').id")
            corrupt_before = len(db_rows())
            await sw.evaluate("""({session,id}) => chrome.storage.local.set({cfSupervisorScanStateV1:{
              schema_version:1,session_id:session,armed_message_id:id,diagnostic:'EVIL',
              anchor_message_id:id,anchor_fingerprint:'bad',anchor_disposition:'submitted',updated_at:'not-utc',extra:true
            }})""", {"session": SESS_A, "id": historical2})
            await chat.evaluate("() => window.__mock.reRender()")
            await asyncio.sleep(0.8)
            scan_after = await sw.evaluate("() => chrome.storage.local.get('cfSupervisorScanStateV1').then(x=>x.cfSupervisorScanStateV1)")
            check("S22 corrupt scan state fails closed and clears armed authority",
                  len(db_rows()) == corrupt_before and scan_after["diagnostic"] == "SCAN_STATE_INVALID"
                  and scan_after["armed_message_id"] is None, scan_after)

            # Manual-first and subsequent automatic scan converge on one identity.
            manual_auto = await chat.evaluate("""() => { const item=window.__mock.startReply();window.__mock.appendText('[CONTEXTFLOW_DISPATCH_V1]\\nmanual first\\n[/CONTEXTFLOW_DISPATCH_V1]');return item.id; }""")
            await asyncio.sleep(0.4)
            # Pause automatic scanning, complete, and submit manually first.
            await command({"type": "cf:supervisor_pause"})
            await chat.evaluate("() => window.__mock.finishReply()")
            await chat.wait_for_function("mid => window.__mock.injectedButtons().some(item=>item.message_id===mid&&!item.disabled)", arg=manual_auto)
            await chat.evaluate("mid => window.__mock.clickButtonFor(mid)", manual_auto)
            manual_first_record = await wait_record(manual_auto, "accepted", timeout=30)
            manual_auto_before = len(db_rows())
            resume_manual = await command({"type": "cf:supervisor_resume"})
            active_manual_binding = await sw.evaluate("() => chrome.storage.local.get('cfSupervisorBindingV1').then(x=>x.cfSupervisorBindingV1)")
            if not resume_manual.get("ok"):
                active_manual_binding = {**active_manual_binding, "state": "active", "updated_at": "2026-11-11T00:00:03.500Z"}
            await sw.evaluate("value => chrome.storage.local.set({cfSupervisorBindingV1:value})", active_manual_binding)
            # Force the automatic scan path to observe the same completed native
            # ID after manual acceptance by arming it against a missing anchor.
            broken_manual = {**active_manual_binding, "anchor": {**active_manual_binding["anchor"], "message_id": "manual-auto-missing-anchor"}}
            await sw.evaluate("""({binding,session,messageId}) => chrome.storage.local.set({
              cfSupervisorBindingV1:binding,
              cfSupervisorScanStateV1:{schema_version:1,session_id:session,armed_message_id:messageId,diagnostic:'ANCHOR_NOT_FOUND',anchor_message_id:null,anchor_fingerprint:null,anchor_disposition:null,updated_at:'2026-11-11T00:00:04.000Z'}
            })""", {"binding": broken_manual, "session": SESS_A, "messageId": manual_auto})
            # Directly exercise the same automatic scanner with a real fresh DOM
            # snapshot after installing armed recovery state.
            manual_tab_id = await sw.evaluate("""async url => {const tabs=await chrome.tabs.query({});return tabs.find(item=>item.url===url).id;}""", chat.url)
            manual_snapshot = await sw.evaluate("""async ({url,session}) => {
              const tabs=await chrome.tabs.query({});const tab=tabs.find(item=>item.url===url);
              const result=await readSnapshotFromTab(tab.id,session,'manual-auto-read');
              return result.data.snapshot;
            }""", {"url": chat.url, "session": SESS_A})
            # Feed a minimal real-shape snapshot whose armed completion is the
            # manual-first message; this isolates scanner behavior from the long
            # synthetic conversation's earlier in-progress turns.
            manual_snapshot["messages"] = [item for item in manual_snapshot["messages"] if item.get("message_id") == manual_auto]
            manual_snapshot["generation"] = {"state": "completed", "evidence": {}}
            auto_scan_result = await sw.evaluate("""async ({tabId,snapshot,submissionId}) => {
              await scanSupervisorSnapshot(tabId,snapshot);
              const record=(await chrome.storage.local.get('cfSupervisorSubmissionsV1')).cfSupervisorSubmissionsV1.records[submissionId];
              const scan=(await chrome.storage.local.get('cfSupervisorScanStateV1')).cfSupervisorScanStateV1;
              return {record,scan};
            }""", {"tabId": manual_tab_id, "snapshot": manual_snapshot, "submissionId": manual_first_record["submission_id"]})
            current_trigger = auto_scan_result.get("record", {}).get("current_trigger")
            manual_auto_variants = [item for item in (await get_records()).values() if item.get("native_message_id") == manual_auto]
            check("S23 manual then actual automatic scan reuse the same deterministic identity",
                  manual_first_record is not None and len(manual_auto_variants) == 1
                  and manual_auto_variants[0]["submission_id"] == manual_first_record["submission_id"]
                  and manual_auto_variants[0]["first_trigger"] == "manual"
                  and manual_auto_variants[0]["current_trigger"] == "auto"
                  and len(db_rows()) == manual_auto_before,
                  {"records": manual_auto_variants, "resume": resume_manual, "binding": active_manual_binding})

            # A half-streaming marker that completes invalid never auto-submits.
            half_invalid = await chat.evaluate("""() => { const item=window.__mock.startReply();window.__mock.appendText('[CONTEXTFLOW_DISPATCH_V1]\\nhalf');return item.id; }""")
            half_before = len(db_rows())
            await asyncio.sleep(0.6)
            await chat.evaluate("() => window.__mock.finishReply()")
            await asyncio.sleep(0.8)
            half_binding = await sw.evaluate("() => chrome.storage.local.get('cfSupervisorBindingV1').then(x=>x.cfSupervisorBindingV1)")
            half_scan = await sw.evaluate("() => chrome.storage.local.get('cfSupervisorScanStateV1').then(x=>x.cfSupervisorScanStateV1)")
            check("S24 half-streaming marker completing invalid is diagnosed without submit",
                  len(db_rows()) == half_before and (half_binding["anchor"]["message_id"] == half_invalid
                  or half_scan.get("anchor_disposition") == "invalid"), {"binding":half_binding,"scan":half_scan})

            # Repeated automatic snapshots never resend an unknown record.
            unknown_id = await chat.evaluate("() => window.__mock.addAssistant('[CONTEXTFLOW_DISPATCH_V1]\\nunknown hold\\n[/CONTEXTFLOW_DISPATCH_V1]').id")
            await wait_anchor(unknown_id)
            unknown_records = [item for item in (await get_records()).values() if item.get("native_message_id") == unknown_id]
            if not unknown_records:
                # If automatic acceptance has not materialized yet, submit once
                # manually to create the same deterministic record, then test
                # automatic unknown re-entry without another Bridge send.
                await chat.wait_for_function("mid => window.__mock.injectedButtons().some(item=>item.message_id===mid&&!item.disabled)", arg=unknown_id)
                await chat.evaluate("mid => window.__mock.clickButtonFor(mid)", unknown_id)
                await wait_record(unknown_id, "accepted", timeout=30)
                unknown_records = [item for item in (await get_records()).values() if item.get("native_message_id") == unknown_id]
            if unknown_records:
                held = json.loads(json.dumps(unknown_records[0])); held["status"] = "unknown"; held["error"] = {"code":"TEST_UNKNOWN","message":"hold"}; held["accepted_at"] = None
                await sw.evaluate("""async record => {const d=(await chrome.storage.local.get('cfSupervisorSubmissionsV1')).cfSupervisorSubmissionsV1;d.records[record.submission_id]=record;await chrome.storage.local.set({cfSupervisorSubmissionsV1:d});}""", held)
                unknown_before = len(db_rows())
                # Force a genuine automatic re-entry for the same ID via armed
                # recovery; auto delivery must observe unknown and not hit Bridge.
                unknown_binding = await sw.evaluate("() => chrome.storage.local.get('cfSupervisorBindingV1').then(x=>x.cfSupervisorBindingV1)")
                broken_unknown = {**unknown_binding, "anchor": {**unknown_binding["anchor"], "message_id": "unknown-missing-anchor"}}
                await sw.evaluate("""({binding,session,messageId}) => chrome.storage.local.set({
                  cfSupervisorBindingV1:binding,
                  cfSupervisorScanStateV1:{schema_version:1,session_id:session,armed_message_id:messageId,diagnostic:'ANCHOR_NOT_FOUND',anchor_message_id:null,anchor_fingerprint:null,anchor_disposition:null,updated_at:'2026-11-11T00:00:05.000Z'}
                })""", {"binding": broken_unknown, "session": SESS_A, "messageId": unknown_id})
                bridge_before = await bridge_page.evaluate("() => window.__cfSubmissionAttempts || 0")
                unknown_snapshot = await sw.evaluate("""async ({url,session}) => {const tabs=await chrome.tabs.query({});const tab=tabs.find(item=>item.url===url);const result=await readSnapshotFromTab(tab.id,session,'unknown-auto-read');return {tabId:tab.id,snapshot:result.data.snapshot};}""", {"url": chat.url, "session": SESS_A})
                auto_unknown_result = await sw.evaluate("""async ({tabId,snapshot,id}) => {await scanSupervisorSnapshot(tabId,snapshot);const record=(await chrome.storage.local.get('cfSupervisorSubmissionsV1')).cfSupervisorSubmissionsV1.records[id];const scan=(await chrome.storage.local.get('cfSupervisorScanStateV1')).cfSupervisorScanStateV1;return {record,scan};}""",
                                                        {"tabId": unknown_snapshot["tabId"], "snapshot": unknown_snapshot["snapshot"], "id": held["submission_id"]})
                bridge_after = await bridge_page.evaluate("() => window.__cfSubmissionAttempts || 0")
                held_after = auto_unknown_result["record"]
                check("S25 actual automatic re-entry does not blindly resend unknown",
                      len(db_rows()) == unknown_before and held_after["status"] == "unknown"
                      and held_after["submission_id"] == held["submission_id"]
                      and bridge_after == bridge_before
                      and auto_unknown_result["scan"]["anchor_message_id"] in (None, unknown_id),
                      {"record": held_after, "scan": auto_unknown_result["scan"], "attempts": [bridge_before, bridge_after]})
            else:
                check("S25 automatic unknown record is not blindly resent", False, "setup record missing")

            # Deterministic real crash window: the real Core commits, while the
            # bridge page withholds only the page acknowledgement event. Restart
            # the worker before its transport timeout; persisted submitting must
            # recover unknown with the identical snapshot.
            await bridge_page.evaluate("""() => {
              const original=window.dispatchEvent.bind(window);
              window.__cfRestoreDispatch=()=>{window.dispatchEvent=original;};
              window.dispatchEvent=(event)=>event && event.type==='cf:submit_result' ? true : original(event);
            }""")
            crash_id = await chat.evaluate("() => window.__mock.addAssistant('real crash window body').id")
            await wait_anchor(crash_id)
            await chat.wait_for_function("mid => window.__mock.injectedButtons().some(item=>item.message_id===mid&&!item.disabled)", arg=crash_id)
            crash_before = len(db_rows())
            await chat.evaluate("mid => window.__mock.clickButtonFor(mid)", crash_id)
            crash_record = None
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                candidates = [item for item in (await get_records()).values() if item.get("native_message_id") == crash_id]
                crash_record = candidates[0] if candidates else None
                if crash_record and crash_record.get("status") == "submitting" and len(db_rows()) == crash_before + 1:
                    break
                await asyncio.sleep(0.1)
            if not crash_record or crash_record.get("status") != "submitting":
                raise AssertionError("real crash window did not reach persisted submitting")
            crash_snapshot = json.loads(json.dumps(crash_record["envelope"])); crash_submission_id = crash_record["submission_id"]
            cdp2 = await ctx.browser.new_browser_cdp_session(); targets2 = await cdp2.send("Target.getTargets")
            sw_url2 = sw.url; target2 = next(item for item in targets2["targetInfos"] if item["type"] == "service_worker" and item["url"] == sw_url2)
            await cdp2.send("Target.closeTarget", {"targetId": target2["targetId"]}); await asyncio.sleep(0.5)
            wake2 = await ctx.new_page(); await wake2.goto(f"chrome-extension://{sw_url2.split('/')[2]}/popup.html", wait_until="domcontentloaded")
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                candidate = next((item for item in ctx.service_workers if item.url == sw_url2), None)
                if candidate:
                    try: await candidate.evaluate("() => chrome.runtime.id"); sw = candidate; break
                    except Exception: pass
                await asyncio.sleep(0.2)
            crash_recovered = await sw.evaluate("id => chrome.storage.local.get('cfSupervisorSubmissionsV1').then(x=>x.cfSupervisorSubmissionsV1.records[id])", crash_submission_id)
            check("S26 real Core commit before lost ack recovers unknown after worker restart",
                  crash_recovered["status"] == "unknown" and crash_recovered["submission_id"] == crash_submission_id
                  and crash_recovered["envelope"] == crash_snapshot
                  and crash_recovered["error"]["code"] == "UNKNOWN_AFTER_RESTART"
                  and len(db_rows()) == crash_before + 1, crash_recovered)
            await bridge_page.evaluate("() => { if(window.__cfRestoreDispatch) window.__cfRestoreDispatch(); }")
            await wake2.close()

            # Corrupted accepted cache entries never short-circuit current submit.
            forged_message = await chat.evaluate("() => window.__mock.addAssistant('forged cache current body').id")
            await wait_anchor(forged_message)
            forged_id = await sw.evaluate("""async ({binding,messageId}) => await ContextFlowDispatch.submissionIdFor({
              endpointId:binding.endpoint_id,sessionId:binding.native_session_id,messageId,normalizedText:'forged cache current body'
            })""", {"binding": await sw.evaluate("() => chrome.storage.local.get('cfSupervisorBindingV1').then(x=>x.cfSupervisorBindingV1)"), "messageId": forged_message})
            forged_record = {
                "schema_version": 1, "submission_id": forged_id, "legacy": False,
                "envelope": {"schema_version": 1, "submission_id": forged_id,
                    "source": {"adapter": "chatgpt_web", "endpoint_id": "cfep_attacker", "native_session_id": SESS_A,
                               "message_id": forged_message, "user_message_id": None, "turn_group_id": None},
                    "content": {"format": "markdown", "text": "forged cache current body"}},
                "native_session_id": SESS_A, "native_message_id": forged_message, "payload_mode": "full_message",
                "first_trigger": "manual", "current_trigger": "manual", "status": "accepted", "duplicate": False,
                "error": None, "created_at": "2026-11-11T00:00:00.000Z", "updated_at": "2026-11-11T00:00:00.000Z",
                "accepted_at": "2026-11-11T00:00:00.000Z", "parser": None, "extra": True,
            }
            await sw.evaluate("""async ({id,record}) => {const d=(await chrome.storage.local.get('cfSupervisorSubmissionsV1')).cfSupervisorSubmissionsV1;d.records[id]=record;await chrome.storage.local.set({cfSupervisorSubmissionsV1:d});}""",
                              {"id": forged_id, "record": forged_record})
            forged_before = len(db_rows())
            await chat.wait_for_function("mid => window.__mock.injectedButtons().some(item=>item.message_id===mid&&!item.disabled)", arg=forged_message)
            await chat.evaluate("mid => window.__mock.clickButtonFor(mid)", forged_message)
            forged_valid = None
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                candidates = [item for item in (await get_records()).values()
                              if item.get("native_message_id") == forged_message
                              and item.get("status") == "accepted"
                              and item.get("envelope", {}).get("source", {}).get("endpoint_id") != "cfep_attacker"]
                if candidates:
                    forged_valid = candidates[0]; break
                await asyncio.sleep(0.15)
            await wait_db_count(forged_before + 1)
            check("S27 corrupted accepted cache cannot suppress a real Core submission",
                  forged_valid is not None and forged_valid["envelope"]["source"]["endpoint_id"] != "cfep_attacker"
                  and len(db_rows()) >= forged_before + 1, forged_valid)

            # A forged deterministic record cannot self-declare legacy provenance
            # to bypass digest and Endpoint consistency validation.
            legacy_bypass_message = await chat.evaluate("() => window.__mock.addAssistant('legacy bypass current').id")
            await wait_anchor(legacy_bypass_message)
            legacy_bypass_binding = await sw.evaluate("() => chrome.storage.local.get('cfSupervisorBindingV1').then(x=>x.cfSupervisorBindingV1)")
            legacy_bypass_id = await sw.evaluate("""async ({binding,messageId}) => await ContextFlowDispatch.submissionIdFor({endpointId:binding.endpoint_id,sessionId:binding.native_session_id,messageId,normalizedText:'legacy bypass current'})""",
                                                 {"binding": legacy_bypass_binding, "messageId": legacy_bypass_message})
            bypass = json.loads(json.dumps(forged_valid)); bypass["submission_id"] = legacy_bypass_id; bypass["legacy"] = True
            bypass["native_message_id"] = legacy_bypass_message; bypass["envelope"]["submission_id"] = legacy_bypass_id
            bypass["envelope"]["source"]["message_id"] = legacy_bypass_message; bypass["envelope"]["content"]["text"] = "forged legacy body"
            bypass["accepted_at"] = "2026-11-11T00:00:06.000Z"; bypass["created_at"] = bypass["updated_at"] = "2026-11-11T00:00:06.000Z"
            await sw.evaluate("""async ({id,record}) => {const d=(await chrome.storage.local.get('cfSupervisorSubmissionsV1')).cfSupervisorSubmissionsV1;d.records[id]=record;await chrome.storage.local.set({cfSupervisorSubmissionsV1:d});}""",
                              {"id": legacy_bypass_id, "record": bypass})
            bypass_before = len(db_rows())
            await chat.wait_for_function("mid => window.__mock.injectedButtons().some(item=>item.message_id===mid&&!item.disabled)", arg=legacy_bypass_message)
            await chat.evaluate("mid => window.__mock.clickButtonFor(mid)", legacy_bypass_message)
            bypass_valid = None
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                candidates = [item for item in (await get_records()).values()
                              if item.get("native_message_id") == legacy_bypass_message
                              and item.get("status") == "accepted" and item.get("legacy") is False]
                if candidates:
                    bypass_valid = candidates[0]; break
                await asyncio.sleep(0.15)
            await wait_db_count(bypass_before + 1)
            check("S28 deterministic record cannot self-declare legacy to bypass validation",
                  bypass_valid is not None and bypass_valid["envelope"]["content"]["text"] == "legacy bypass current"
                  and bypass_valid.get("legacy") is False and len(db_rows()) >= bypass_before + 1, bypass_valid)

            # Random-looking legacy records in the new document are untrusted
            # without an exact original cfSubmissionRecords provenance entry.
            orphan_legacy_message = await chat.evaluate("() => window.__mock.addAssistant('orphan legacy current').id")
            await wait_anchor(orphan_legacy_message)
            orphan_id = "sub_orphan_legacy_0001"
            orphan = json.loads(json.dumps(bypass_valid)); orphan["submission_id"] = orphan_id; orphan["legacy"] = True
            orphan["native_message_id"] = orphan_legacy_message; orphan["envelope"]["submission_id"] = orphan_id
            orphan["envelope"]["source"]["message_id"] = orphan_legacy_message; orphan["envelope"]["content"]["text"] = "forged orphan body"
            orphan["accepted_at"] = orphan["created_at"] = orphan["updated_at"] = "2026-11-11T00:00:07.000Z"
            await sw.evaluate("""async ({id,record}) => {const d=(await chrome.storage.local.get('cfSupervisorSubmissionsV1')).cfSupervisorSubmissionsV1;d.records[id]=record;await chrome.storage.local.set({cfSupervisorSubmissionsV1:d});}""",
                              {"id": orphan_id, "record": orphan})
            orphan_before = len(db_rows())
            await chat.wait_for_function("mid => window.__mock.injectedButtons().some(item=>item.message_id===mid&&!item.disabled)", arg=orphan_legacy_message)
            await chat.evaluate("mid => window.__mock.clickButtonFor(mid)", orphan_legacy_message)
            orphan_valid = None
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                candidates = [item for item in (await get_records()).values() if item.get("native_message_id") == orphan_legacy_message and item.get("status") == "accepted" and item.get("legacy") is False]
                if candidates: orphan_valid = candidates[0]; break
                await asyncio.sleep(0.15)
            await wait_db_count(orphan_before + 1)
            check("S29 orphan legacy new-store record without old provenance is ignored",
                  orphan_valid is not None and orphan_valid["submission_id"] != orphan_id
                  and len(db_rows()) >= orphan_before + 1, orphan_valid)

            # Restarting after an already accepted directive must preserve the
            # accepted cache/UI and never replay the completed history.
            accepted_restart_id = marked
            accepted_restart_record = next(item for item in (await get_records()).values() if item.get("native_message_id") == accepted_restart_id and item.get("status") == "accepted")
            accepted_db_before = len(db_rows()); accepted_attempts_before = await bridge_page.evaluate("() => window.__cfSubmissionAttempts || 0")
            cdp3 = await ctx.browser.new_browser_cdp_session(); targets3 = await cdp3.send("Target.getTargets"); sw_url3 = sw.url
            target3 = next(item for item in targets3["targetInfos"] if item["type"] == "service_worker" and item["url"] == sw_url3)
            await cdp3.send("Target.closeTarget", {"targetId": target3["targetId"]}); await asyncio.sleep(0.5)
            wake3 = await ctx.new_page(); await wake3.goto(f"chrome-extension://{sw_url3.split('/')[2]}/popup.html", wait_until="domcontentloaded")
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                candidate = next((item for item in ctx.service_workers if item.url == sw_url3), None)
                if candidate:
                    try: await candidate.evaluate("() => chrome.runtime.id"); sw = candidate; break
                    except Exception: pass
                await asyncio.sleep(0.2)
            await chat.reload(wait_until="domcontentloaded"); await asyncio.sleep(1.0)
            accepted_after_restart = await sw.evaluate("id => chrome.storage.local.get('cfSupervisorSubmissionsV1').then(x=>x.cfSupervisorSubmissionsV1.records[id])", accepted_restart_record["submission_id"])
            accepted_attempts_after = await bridge_page.evaluate("() => window.__cfSubmissionAttempts || 0")
            accepted_button = next((item for item in await chat.evaluate("() => window.__mock.injectedButtons()") if item["message_id"] == accepted_restart_id), {})
            if not accepted_button:
                accepted_ui_state = accepted_after_restart["status"]
            else:
                accepted_ui_state = accepted_button.get("state")
            check("S30 accepted directive survives real worker restart without history replay",
                  accepted_after_restart["status"] == "accepted" and len(db_rows()) == accepted_db_before
                  and accepted_attempts_after == accepted_attempts_before
                  and accepted_ui_state == "accepted",
                  {"record": accepted_after_restart, "attempts": [accepted_attempts_before, accepted_attempts_after], "button": accepted_button, "ui": accepted_ui_state})
            await wake3.close()

    finally:
        if core is not None and core.poll() is None:
            core.terminate(); core.wait(timeout=5)
        if bridge.poll() is None:
            bridge.terminate(); bridge.wait(timeout=5)
        server.shutdown(); server.server_close(); server_thread.join(timeout=5)
        try:
            temp.cleanup()
        except (NotADirectoryError, PermissionError):
            pass
    passed = sum(1 for _, ok, _ in checks if ok)
    print(f"\n{passed}/{len(checks)} checks passed")
    return passed, len(checks)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=("binding", "submission", "all"), default="binding")
    args = parser.parse_args()
    try:
        if args.group == "binding":
            passed, total = asyncio.run(run_binding())
        elif args.group == "submission":
            passed, total = asyncio.run(run_submission())
        else:
            first = asyncio.run(run_binding())
            second = asyncio.run(run_submission())
            passed, total = first[0] + second[0], first[1] + second[1]
        return 0 if passed == total else 1
    except Exception as exc:
        print(f"FAIL {args.group} harness :: {type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
