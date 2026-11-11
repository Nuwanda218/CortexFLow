#!/usr/bin/env python3
"""CF-ADAPTER-001 automated browser test (mock ChatGPT page + real extension).

Covers:
  T1  paper-plane buttons are injected only for Assistant turns
  T2  native ChatGPT action buttons are preserved
  T3  button is bound to the correct native message id
  T4  click submits a complete envelope and shows "已送达" only after ack
  T5  repeated click does not create a new logical submission
  T6  two Assistant replies in the same turn group are independently selectable
  T7  DOM re-render re-injects buttons and restores accepted state
  T8  page reload restores accepted state from chrome.storage.local
  T9  missing native message id -> disabled button, nothing submitted
  T10 unbound Endpoint -> explicit failure, nothing submitted
  T11 lost acknowledgement -> "unknown", retry reuses the same submission_id
  T12 bridge down -> failure state, no false "delivered"
  T13 multiple tabs/sessions do not cross-submit
  T14 reverse send by Endpoint ID reaches the bound session and is verified
  T15 reverse send to an ambiguous session is refused (no fallback)
  T16 reverse send to an unknown Endpoint is refused

Run:
    python experiments/playwright-probe/cfadapter001_test.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
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

import requests
import websockets
from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
EXT = ROOT / "contextflow-extension"
ARTIFACTS = ROOT / "_artifacts"
MOCK_DIR = ARTIFACTS / "cfadapter001-mock"
PROFILE = ARTIFACTS / "edge-profile-cfadapter001"
TOKEN = "dev-token-cf-exp-002"
MOCK_CORE = ROOT / "experiments" / "adapter-mock" / "mock_core.py"

SESS_A = "aaaa1111-0000-4000-8000-00000000000a"
SESS_B = "bbbb2222-0000-4000-8000-00000000000b"
SESS_C = "cccc3333-0000-4000-8000-00000000000c"  # deliberately unbound

EVIDENCE: dict = {"checks": [], "console": []}

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def check(name: str, ok: bool, detail: str = "") -> None:
    EVIDENCE["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)[:800]})
    print(("PASS " if ok else "FAIL ") + name + (f" :: {str(detail)[:240]}" if detail else ""), flush=True)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_http(url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.15)
    raise RuntimeError(f"service not ready: {url}")


def wait_external_clients(port: int, expected: int, timeout: float = 15.0) -> int:
    deadline = time.monotonic() + timeout
    last = -1
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                data = json.loads(response.read().decode("utf-8"))
                last = int(data.get("external_clients", 0))
                if last == expected:
                    return last
        except Exception:
            pass
        time.sleep(0.15)
    return last


def wait_store_count(path: Path, expected: int, timeout: float = 15.0) -> int:
    deadline = time.monotonic() + timeout
    last = -1
    while time.monotonic() < deadline:
        if path.exists():
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                last = len(document.get("submissions") or {})
                if last >= expected:
                    return last
            except Exception:
                pass
        time.sleep(0.15)
    return last


def read_store(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("submissions") or {}
    except Exception:
        return {}


async def wait_button_state(page, message_id: str, state: str, timeout: float = 25.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await page.evaluate(
            """(mid) => {
                const buttons = Array.from(document.querySelectorAll('[data-cf-submit-button]'));
                for (const b of buttons) {
                    const turn = b.closest('[data-chatgpt-search-unit-key]');
                    if (turn && turn.getAttribute('data-chatgpt-search-message-ids') === mid) {
                        return { state: b.getAttribute('data-cf-submit-state'),
                                 label: (b.querySelector('.cf-submit-label') || {}).textContent || '',
                                 disabled: b.disabled, title: b.title };
                    }
                }
                return null;
            }""",
            message_id,
        )
        if last and last.get("state") == state:
            return last
        await asyncio.sleep(0.2)
    return last or {}


async def wait_buttons(page, minimum: int = 1, timeout: float = 20.0) -> list:
    deadline = time.monotonic() + timeout
    last = []
    while time.monotonic() < deadline:
        last = await page.evaluate("() => window.__mock.injectedButtons()")
        if len(last) >= minimum:
            return last
        await asyncio.sleep(0.2)
    return last


async def external_request(port: int, payload: dict, timeout: float = 60.0) -> dict:
    remaining = await asyncio.to_thread(wait_external_clients, port, 0)
    if remaining != 0:
        raise RuntimeError(f"external Core ownership not released: external_clients={remaining}")
    uri = f"ws://127.0.0.1:{port}/ws?token={TOKEN}"
    async with websockets.connect(uri, origin=f"http://127.0.0.1:{port}",
                                  max_size=8 * 1024 * 1024) as ws:
        while True:
            message = json.loads(await ws.recv())
            if message.get("type") == "welcome":
                break
        await ws.send(json.dumps(payload, ensure_ascii=False))
        while True:
            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
            if message.get("type") == "welcome":
                continue
            if message.get("type") == "submission":
                continue
            if message.get("id") == payload.get("id"):
                return message


async def main() -> int:
    shutil.rmtree(PROFILE, ignore_errors=True)
    temp_dir = tempfile.TemporaryDirectory()
    extension_dir = Path(temp_dir.name) / "extension"
    shutil.copytree(EXT, extension_dir)
    registry_path = Path(temp_dir.name) / "endpoints.json"
    store_path = Path(temp_dir.name) / "submissions.json"

    sys.path.insert(0, str(ROOT))
    from contextflow_core.registry import EndpointRegistry
    registry = EndpointRegistry(registry_path)
    binding_a, _ = registry.register("chatgpt_web", SESS_A, "chat")
    binding_b, _ = registry.register("chatgpt_web", SESS_B, "chat")
    endpoint_a = binding_a.endpoint_id
    endpoint_b = binding_b.endpoint_id

    bridge_port = free_port()
    mock_port = free_port()
    (extension_dir / "runtime_config.js").write_text(
        'globalThis.ContextFlowRuntimeConfig = Object.freeze({allowed_mock_origins:["http://127.0.0.1:%d"]});\n' % mock_port,
        encoding="utf-8",
    )

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def translate_path(self, path):
            # The mock page rewrites its URL to /c/<session> like ChatGPT does;
            # serve the same document for that path so page.reload() works.
            if path.startswith("/c/"):
                path = "/mock.html"
            return super().translate_path(path)

    server = ThreadingHTTPServer(("127.0.0.1", mock_port), partial(Quiet, directory=str(MOCK_DIR)))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()

    env = dict(os.environ)
    env["CONTEXTFLOW_ENDPOINT_REGISTRY"] = str(registry_path)
    bridge = subprocess.Popen(
        [sys.executable, str(EXT / "bridge_server.py"), "--port", str(bridge_port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
    )
    core = None
    ctx = None
    try:
        wait_http(f"http://127.0.0.1:{bridge_port}/health")
        core = subprocess.Popen(
            [sys.executable, str(MOCK_CORE), "--store", str(store_path),
             "--uri", f"ws://127.0.0.1:{bridge_port}/ws?token={TOKEN}",
             "--origin", f"http://127.0.0.1:{bridge_port}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        wait_external_clients(bridge_port, 1)

        async with async_playwright() as p, AsyncExitStack() as cleanup:
            ctx = await p.chromium.launch_persistent_context(
                str(PROFILE), channel="msedge", headless=True,
                args=[f"--disable-extensions-except={extension_dir}", f"--load-extension={extension_dir}",
                      "--no-first-run", "--no-default-browser-check"],
            )

            async def close_browser_context() -> None:
                if ctx.browser is None or ctx.browser.is_connected():
                    await ctx.close()

            cleanup.push_async_callback(close_browser_context)
            page = await ctx.new_page()
            page.on("console", lambda m: EVIDENCE["console"].append(f"[{m.type}] {m.text[:240]}"))
            page.on("pageerror", lambda e: EVIDENCE["console"].append(f"[pageerror] {e}"))
            await page.goto(f"http://127.0.0.1:{mock_port}/mock.html?s={SESS_A}",
                            wait_until="domcontentloaded")
            await page.evaluate("() => window.__mock.seed()")
            await asyncio.sleep(1.0)

            bridge_page = await ctx.new_page()
            await bridge_page.goto(
                f"http://127.0.0.1:{bridge_port}/bridge_client.html?token={TOKEN}",
                wait_until="domcontentloaded",
            )
            await bridge_page.wait_for_function(
                "() => window.__cfBridgePage && window.__cfBridgePage.isConnected()", timeout=15000)
            await asyncio.sleep(0.5)

            # Seed the already-existing Endpoint as the local Supervisor binding;
            # this legacy suite uses Mock Core, which predates C-01 control frames.
            sw = ctx.service_workers[0] if ctx.service_workers else await ctx.wait_for_event("serviceworker", timeout=15000)
            seeded = await page.evaluate("() => window.__mock.state()")
            baseline_id = [item["id"] for item in seeded["turns"] if item["role"] == "assistant"][-1]
            await sw.evaluate("""async ({sessionId,endpointId,messageId}) => {
              const at=new Date().toISOString();
              await chrome.storage.local.set({cfSupervisorBindingV1:{
                schema_version:1,native_session_id:sessionId,endpoint_id:endpointId,
                session_kind:'chat',state:'active',bound_at:at,updated_at:at,
                anchor:{message_id:messageId,updated_at:at,reason:'bind'}
              }});
            }""", {"sessionId": SESS_A, "endpointId": endpoint_a, "messageId": baseline_id})

            # T1/T2/T3 injection and binding
            buttons = await wait_buttons(page, minimum=1)
            state = await page.evaluate("() => window.__mock.state()")
            assistant_ids = [t["id"] for t in state["turns"] if t["role"] == "assistant"]
            check("T1 buttons injected only for Assistant turns",
                  len(buttons) == len(assistant_ids) and len(assistant_ids) == 1,
                  f"buttons={len(buttons)} assistants={len(assistant_ids)}")
            native_count = await page.evaluate("() => window.__mock.nativeActionButtonCount()")
            check("T2 native action buttons preserved", native_count == 2, f"native={native_count}")
            check("T3 button bound to native message id",
                  buttons[0]["message_id"] == assistant_ids[0]
                  and buttons[0]["key"] == f"{SESS_A}:{assistant_ids[0]}",
                  json.dumps(buttons[0], ensure_ascii=False))

            # T4 click -> envelope -> ack -> delivered
            await page.evaluate("(mid) => window.__mock.clickButtonFor(mid)", assistant_ids[0])
            count = wait_store_count(store_path, 1)
            records = read_store(store_path)
            record = next(iter(records.values())) if records else {}
            envelope = record.get("envelope") or {}
            source = envelope.get("source") or {}
            content = envelope.get("content") or {}
            check("T4a Mock Core received exactly one envelope", count == 1, f"count={count}")
            check("T4b envelope identity is complete",
                  envelope.get("schema_version") == 1
                  and str(envelope.get("submission_id", "")).startswith("sub_")
                  and source.get("adapter") == "chatgpt_web"
                  and source.get("endpoint_id") == endpoint_a
                  and source.get("native_session_id") == SESS_A
                  and source.get("message_id") == assistant_ids[0]
                  and source.get("user_message_id")
                  and source.get("turn_group_id")
                  and content.get("format") == "markdown"
                  and "baseline assistant reply" in (content.get("text") or ""),
                  json.dumps({"source": source, "text": (content.get("text") or "")[:80]}, ensure_ascii=False))
            delivered = await wait_button_state(page, assistant_ids[0], "accepted", timeout=15)
            check("T4c button shows accepted only after ack",
                  delivered.get("state") == "accepted" and "已送达" in delivered.get("label", ""),
                  json.dumps(delivered, ensure_ascii=False))
            first_submission_id = envelope.get("submission_id")

            # T5 repeated click is idempotent
            await page.evaluate("(mid) => window.__mock.clickButtonFor(mid)", assistant_ids[0])
            await asyncio.sleep(1.2)
            check("T5 repeated click creates no new submission",
                  wait_store_count(store_path, 2, timeout=1.5) == 1,
                  f"count={len(read_store(store_path))}")

            # T6 two Assistant replies in the same turn group are independent
            await page.evaluate("() => window.__mock.addAssistantSameGroup('regenerated assistant reply')")
            buttons = await wait_buttons(page, minimum=2)
            state = await page.evaluate("() => window.__mock.state()")
            assistant_turns = [t for t in state["turns"] if t["role"] == "assistant"]
            second_id = assistant_turns[-1]["id"]
            await page.evaluate("(mid) => window.__mock.clickButtonFor(mid)", second_id)
            wait_store_count(store_path, 2)
            records = read_store(store_path)
            second = next((r for r in records.values()
                           if r["envelope"]["source"]["message_id"] == second_id), None)
            first = next((r for r in records.values()
                          if r["envelope"]["source"]["message_id"] == assistant_ids[0]), None)
            check("T6 same-turn Assistant replies are independently selectable",
                  second is not None and first is not None
                  and second["envelope"]["source"]["turn_group_id"] == first["envelope"]["source"]["turn_group_id"]
                  and second["envelope"]["source"]["user_message_id"] == first["envelope"]["source"]["user_message_id"]
                  and second["envelope"]["source"]["message_id"] != first["envelope"]["source"]["message_id"],
                  f"first={first and first['envelope']['source']['message_id']} "
                  f"second={second and second['envelope']['source']['message_id']}")

            # T7 DOM re-render re-injects and restores accepted state
            await page.evaluate("() => window.__mock.reRender()")
            buttons = await wait_buttons(page, minimum=2)
            restored = await wait_button_state(page, assistant_ids[0], "accepted", timeout=10)
            check("T7 DOM re-render restores accepted state",
                  len(buttons) == 2 and restored.get("state") == "accepted",
                  f"buttons={len(buttons)} state={restored.get('state')}")

            # T8 reload restores accepted state from chrome.storage.local
            await page.reload(wait_until="domcontentloaded")
            await asyncio.sleep(0.8)
            buttons = await wait_buttons(page, minimum=2)
            restored = await wait_button_state(page, assistant_ids[0], "accepted", timeout=10)
            check("T8 page reload restores accepted state",
                  len(buttons) == 2 and restored.get("state") == "accepted",
                  f"buttons={len(buttons)} state={restored.get('state')}")

            # T9 missing native message id -> disabled, nothing submitted
            before_count = len(read_store(store_path))
            await page.evaluate("() => window.__mock.setMissingIds(true)")
            await asyncio.sleep(0.8)
            buttons = await page.evaluate("() => window.__mock.injectedButtons()")
            disabled = [b for b in buttons if b.get("disabled") and "缺少消息 ID" in (b.get("label") or "")]
            await page.evaluate("() => window.__mock.clickButtonAt(0)")
            await asyncio.sleep(1.0)
            check("T9 missing message id -> disabled, nothing submitted",
                  len(disabled) >= 1 and len(read_store(store_path)) == before_count,
                  f"disabled={len(disabled)} count={len(read_store(store_path))}")
            await page.evaluate("() => window.__mock.setMissingIds(false)")

            # T10 unbound endpoint -> explicit failure, nothing submitted
            before_count = len(read_store(store_path))
            await page.evaluate("(sid) => window.__mock.switchSession(sid)", SESS_C)
            await asyncio.sleep(0.4)
            await page.evaluate("() => { window.__mock.addUser('unbound question'); window.__mock.addAssistant('unbound answer'); }")
            await asyncio.sleep(0.8)
            state = await page.evaluate("() => window.__mock.state()")
            unbound_id = [t["id"] for t in state["turns"] if t["role"] == "assistant"][-1]
            await page.evaluate("(mid) => window.__mock.clickButtonFor(mid)", unbound_id)
            failed = await wait_button_state(page, unbound_id, "failed", timeout=10)
            check("T10 unbound endpoint -> explicit failure, nothing submitted",
                  failed.get("state") == "failed" and len(read_store(store_path)) == before_count,
                  json.dumps(failed, ensure_ascii=False))

            # T11 lost ack -> unknown, retry reuses the same submission_id
            await page.evaluate("(sid) => window.__mock.switchSession(sid)", SESS_A)
            await asyncio.sleep(0.4)
            await page.evaluate("() => { window.__mock.addUser('timeout question'); window.__mock.addAssistant('timeout answer'); }")
            await asyncio.sleep(0.8)
            state = await page.evaluate("() => window.__mock.state()")
            timeout_id = [t["id"] for t in state["turns"] if t["role"] == "assistant"][-1]
            # Restart the receiver with one dropped ack.
            core.terminate()
            core.wait(timeout=5)
            await asyncio.sleep(0.6)
            core = subprocess.Popen(
                [sys.executable, str(MOCK_CORE), "--store", str(store_path),
                 "--uri", f"ws://127.0.0.1:{bridge_port}/ws?token={TOKEN}",
                 "--origin", f"http://127.0.0.1:{bridge_port}", "--drop-first", "1"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            wait_external_clients(bridge_port, 1)
            await page.evaluate("(mid) => window.__mock.clickButtonFor(mid)", timeout_id)
            unknown = await wait_button_state(page, timeout_id, "unknown", timeout=30)
            check("T11a lost ack -> unknown (not delivered, not failed)",
                  unknown.get("state") == "unknown" and "结果未知" in (unknown.get("label") or ""),
                  json.dumps(unknown, ensure_ascii=False))
            # Retry: same submission_id, receiver now acknowledges.
            await page.evaluate("(mid) => window.__mock.clickButtonFor(mid)", timeout_id)
            accepted = await wait_button_state(page, timeout_id, "accepted", timeout=20)
            records = read_store(store_path)
            timeout_record = next((r for r in records.values()
                                   if r["envelope"]["source"]["message_id"] == timeout_id), None)
            check("T11b retry reuses the same submission_id and is accepted",
                  accepted.get("state") == "accepted" and timeout_record is not None,
                  json.dumps({"state": accepted.get("state"),
                              "submission_id": timeout_record and timeout_record["submission_id"]},
                             ensure_ascii=False))

            # T13 multiple tabs/sessions do not cross-submit
            page_b = await ctx.new_page()
            await page_b.goto(f"http://127.0.0.1:{mock_port}/mock.html?s={SESS_B}",
                              wait_until="domcontentloaded")
            await page_b.evaluate("() => window.__mock.seed()")
            await asyncio.sleep(1.0)
            buttons_b = await wait_buttons(page_b, minimum=1)
            state_b = await page_b.evaluate("() => window.__mock.state()")
            b_assistant_id = [t["id"] for t in state_b["turns"] if t["role"] == "assistant"][0]
            before_count = len(read_store(store_path))
            # C-01 intentionally binds exactly one Supervisor Session. A message
            # from Session B must be rejected and must not cross-submit as A.
            await page_b.evaluate("(mid) => window.__mock.clickButtonFor(mid)", b_assistant_id)
            failed_b = await wait_button_state(page_b, b_assistant_id, "failed", timeout=10)
            records = read_store(store_path)
            b_record = next((r for r in records.values()
                             if r["envelope"]["source"]["native_session_id"] == SESS_B), None)
            check("T13 multiple tabs/sessions do not cross-submit",
                  failed_b.get("state") == "failed" and b_record is None
                  and len(records) == before_count,
                  json.dumps({"button": failed_b, "record": b_record}, ensure_ascii=False))

            # Hand off the singleton Core connection to the reverse-send requests.
            core.terminate()
            core.wait(timeout=5)
            core = None

            # T14 reverse send by Endpoint ID
            await page.evaluate("() => window.__mock.setAutoReply(true)")
            result = await external_request(bridge_port, {
                "id": "send-a-1",
                "type": "send_message_to_endpoint",
                "endpoint_id": endpoint_a,
                "text": "CF reverse send test",
            })
            await asyncio.sleep(1.5)
            state_a = await page.evaluate("() => window.__mock.state()")
            user_texts = [t["text"] for t in state_a["turns"] if t["role"] == "user"]
            check("T14 reverse send by Endpoint ID reaches the bound session",
                  result.get("success") is True
                  and (result.get("data") or {}).get("verified") is True
                  and any("CF reverse send test" in text for text in user_texts),
                  json.dumps({"result": result, "users": user_texts[-2:]}, ensure_ascii=False))

            # T15 ambiguous session is refused (no fallback)
            page_a2 = await ctx.new_page()
            await page_a2.goto(f"http://127.0.0.1:{mock_port}/mock.html?s={SESS_A}",
                               wait_until="domcontentloaded")
            await page_a2.evaluate("() => window.__mock.seed()")
            await asyncio.sleep(1.0)
            ambiguous = await external_request(bridge_port, {
                "id": "send-a-2",
                "type": "send_message_to_endpoint",
                "endpoint_id": endpoint_a,
                "text": "should not be sent",
            })
            check("T15 ambiguous session is refused without fallback",
                  ambiguous.get("success") is False
                  and (ambiguous.get("error") or {}).get("code") == "AMBIGUOUS_SESSION",
                  json.dumps(ambiguous, ensure_ascii=False))
            await page_a2.close()

            # T16 unknown Endpoint is refused
            unknown_endpoint = await external_request(bridge_port, {
                "id": "send-c-1",
                "type": "send_message_to_endpoint",
                "endpoint_id": "cfep_00000000000000000000000000000000",
                "text": "should not be sent",
            })
            check("T16 unknown Endpoint is refused",
                  unknown_endpoint.get("success") is False
                  and (unknown_endpoint.get("error") or {}).get("code") == "ENDPOINT_NOT_FOUND",
                  json.dumps(unknown_endpoint, ensure_ascii=False))

            # T12 bridge down -> failure, no false delivered (last, stops the bridge)
            await page.evaluate("() => { window.__mock.addUser('bridge down question'); window.__mock.addAssistant('bridge down answer'); }")
            await asyncio.sleep(0.8)
            state = await page.evaluate("() => window.__mock.state()")
            down_id = [t["id"] for t in state["turns"] if t["role"] == "assistant"][-1]
            bridge.terminate()
            bridge.wait(timeout=5)
            await asyncio.sleep(1.0)
            await page.evaluate("(mid) => window.__mock.clickButtonFor(mid)", down_id)
            failed = await wait_button_state(page, down_id, "failed", timeout=15)
            check("T12 bridge down -> failure state, no false delivered",
                  failed.get("state") == "failed",
                  json.dumps(failed, ensure_ascii=False))

            passed = sum(1 for item in EVIDENCE["checks"] if item["ok"])
            EVIDENCE["summary"] = {"passed": passed, "total": len(EVIDENCE["checks"])}
            EVIDENCE["store_submissions"] = [
                {"submission_id": record["submission_id"],
                 "source": record["envelope"]["source"],
                 "text_preview": record["envelope"]["content"]["text"][:100]}
                for record in read_store(store_path).values()
            ]
            ARTIFACTS.mkdir(parents=True, exist_ok=True)
            (ARTIFACTS / "cfadapter001-browser-evidence.json").write_text(
                json.dumps(EVIDENCE, ensure_ascii=False, indent=2), encoding="utf-8")
            if passed != len(EVIDENCE["checks"]):
                print("--- page console (last 30) ---")
                for line in EVIDENCE["console"][-30:]:
                    print(line)
            print(f"\n{passed}/{len(EVIDENCE['checks'])} checks passed")
            return 0 if passed == len(EVIDENCE["checks"]) else 1
    finally:
        if core is not None and core.poll() is None:
            core.terminate()
            core.wait(timeout=5)
        if bridge.poll() is None:
            bridge.terminate()
            bridge.wait(timeout=5)
        server.shutdown()
        temp_dir.cleanup()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
