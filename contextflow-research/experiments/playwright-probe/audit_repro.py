#!/usr/bin/env python3
"""CF-AUDIT-REPRO: non-destructive reproduction tests for the ContextFlow Edge probe.

Tests (all local, no real ChatGPT account, no message sending to ChatGPT):
  A1 extension loads (MV3 service worker)
  A2 page identification on the local mock chat page
  A3 SPA navigation detection (webNavigation.onHistoryStateUpdated)
  A4 message reading on the mock page (counts/roles/status)
  A5 Core -> extension -> content DOM notice (bidirectional bridge)
  A6 session routing behaviour with two tabs (audit finding: routing bug)
  A7 reconnect after Core restart
  A8 fake-clipboard injection into a ProseMirror-like editor
  A9 content script on the real chatgpt.com origin (public challenge page only)

Requires: mock-chat server on 8787 (started by caller).
Core is started/stopped by this script on 8765.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests
from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
EXT = HERE.parent / "edge-extension-probe"
CORE_PY = HERE.parent / "local-core-bridge" / "core_server.py"
ARTIFACTS = ROOT / "_artifacts"
PROFILE = ARTIFACTS / "edge-profile-audit"
MOCK_A = "http://127.0.0.1:8787/mock/chat/session-a"
MOCK_B = "http://127.0.0.1:8787/mock/chat/session-b"
PM_LIKE = "http://127.0.0.1:8787/prosemirror-like.html"
CORE = "http://127.0.0.1:8765"
TOKEN = "dev-token-cf-exp-001"

EVIDENCE: dict = {"checks": [], "notes": []}


def check(name, ok, detail=""):
    EVIDENCE["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail else ""), flush=True)


def note(msg):
    EVIDENCE["notes"].append(msg)
    print("NOTE " + msg, flush=True)


def start_core():
    proc = subprocess.Popen([sys.executable, str(CORE_PY), "--port", "8765"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            if requests.get(f"{CORE}/health", timeout=1).status_code == 200:
                return proc
        except Exception:
            time.sleep(0.3)
    raise RuntimeError("core did not start")


def stop_core(proc):
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


async def wait_ws(sw, status, timeout=40):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            st = await sw.evaluate("() => __cf.getState()")
            last = st.get("wsStatus")
            if last == status:
                return st
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return None


async def tab_state(sw, url_part):
    st = await sw.evaluate("() => __cf.getState()")
    for t in (st.get("tabs") or {}).values():
        if url_part in (t.get("url") or ""):
            return t
    return None


async def main() -> int:
    shutil.rmtree(PROFILE, ignore_errors=True)
    core = start_core()
    try:
        async with async_playwright() as p:
            ctx = await p.chromium.launch_persistent_context(
                str(PROFILE), channel="msedge", headless=True,
                args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}",
                      "--no-first-run", "--no-default-browser-check"],
            )
            sw = ctx.service_workers[0] if ctx.service_workers else await ctx.wait_for_event("serviceworker", timeout=15000)
            ext_id = sw.url.split("/")[2]
            EVIDENCE["extension_id"] = ext_id
            EVIDENCE["browser"] = ctx.browser.version if ctx.browser else None
            manifest = await sw.evaluate("() => __cf.manifest()")
            EVIDENCE["manifest"] = manifest
            check("A1-01 MV3 extension loaded, service worker present", manifest.get("manifest_version") == 3, f"id={ext_id}")

            # A2 page identification on mock page
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(MOCK_A, wait_until="domcontentloaded")
            await page.wait_for_selector("#conversation .message")
            st = None
            for _ in range(40):
                st = await tab_state(sw, "127.0.0.1:8787")
                if st:
                    break
                await asyncio.sleep(0.25)
            EVIDENCE["mock_page_state"] = st
            check("A2-01 content script reports page kind", bool(st) and st.get("kind") == "mock-chat", json.dumps(st)[:300] if st else "no state")
            check("A2-02 session reference from mock DOM", bool(st) and (st.get("session") or {}).get("value") == "session-a", json.dumps((st or {}).get("session")))
            check("A2-03 message counts by role", bool(st) and (st.get("messages") or {}).get("total") == 2, json.dumps((st or {}).get("messages")))

            # A3 SPA navigation
            await page.click('a.session-link[data-session-id="session-b"]')
            await page.wait_for_function("() => document.body.dataset.sessionId === 'session-b'")
            await asyncio.sleep(0.8)
            nav = await sw.evaluate("() => __cf.getState().navEvents")
            hist = [e for e in nav if e.get("kind") == "webNavigation.onHistoryStateUpdated"]
            EVIDENCE["nav_events_sample"] = nav[-8:]
            check("A3-01 pushState detected by webNavigation", bool(hist), f"{len(hist)} event(s)")
            st_b = await tab_state(sw, "127.0.0.1:8787")
            check("A3-02 content script followed SPA navigation", bool(st_b) and (st_b.get("session") or {}).get("value") == "session-b", json.dumps((st_b or {}).get("session")))

            # A4 message reading on mock page (send + wait)
            await page.fill("#prompt-input", "audit message")
            await page.click("#send")
            await page.wait_for_function("() => document.body.dataset.appState === 'idle'", timeout=15000)
            await asyncio.sleep(0.8)
            st4 = await tab_state(sw, "127.0.0.1:8787")
            EVIDENCE["mock_after_reply"] = st4
            check("A4-01 message count after reply", bool(st4) and (st4.get("messages") or {}).get("total") == 4, json.dumps((st4 or {}).get("messages")))
            check("A4-02 last message status complete", bool(st4) and (st4.get("messages") or {}).get("lastStatus") == "complete", json.dumps((st4 or {}).get("messages")))
            check("A4-03 user/assistant split", bool(st4) and (st4.get("messages") or {}).get("byRole", {}).get("user") == 2 and (st4.get("messages") or {}).get("byRole", {}).get("assistant") == 2, json.dumps((st4 or {}).get("messages")))

            # A5 Core -> extension -> content
            ws = await wait_ws(sw, "connected", timeout=25)
            check("A5-01 extension connected to Core over WebSocket", bool(ws), json.dumps(ws)[:200] if ws else "not connected")
            health = requests.get(f"{CORE}/health", timeout=5).json()
            EVIDENCE["core_health"] = health
            check("A5-02 Core sees the extension client", len(health.get("clients") or []) >= 1, json.dumps(health)[:200])
            r = requests.post(f"{CORE}/api/notify", json={"task_id": "audit-task-001", "summary": "audit push"},
                              headers={"X-CF-Token": TOKEN}, timeout=5).json()
            EVIDENCE["notify_response"] = r
            check("A5-03 Core accepted push", r.get("delivered") == 1, json.dumps(r))
            try:
                await page.wait_for_selector("#cf-core-notice", timeout=8000)
                banner = await page.text_content("#cf-core-notice")
                check("A5-04 content DOM notice rendered", "audit-task-001" in (banner or ""), (banner or "")[:160])
            except Exception as exc:  # noqa: BLE001
                check("A5-04 content DOM notice rendered", False, repr(exc))

            # A6 routing with two tabs: does session_ref route to the right tab?
            page2 = await ctx.new_page()
            await page2.goto(MOCK_A, wait_until="domcontentloaded")
            await page2.wait_for_selector("#conversation .message")
            await asyncio.sleep(1.0)
            # Clear stale notices from A5 so the routing result is unambiguous.
            for pg in (page, page2):
                await pg.evaluate("() => document.querySelectorAll('#cf-core-notice').forEach(n => n.remove())")
            # Make page2 (session-a) the active tab, then push a notice for session-b.
            await page2.bring_to_front()
            await asyncio.sleep(0.5)
            requests.post(f"{CORE}/api/notify", json={"task_id": "audit-route-b", "session_ref": "session-b"},
                          headers={"X-CF-Token": TOKEN}, timeout=5)
            await asyncio.sleep(2.0)
            on_b = await page.locator("#cf-core-notice").count()
            on_a = await page2.locator("#cf-core-notice").count()
            EVIDENCE["routing_test"] = {"notice_on_session_b_tab": on_b, "notice_on_session_a_tab": on_a,
                                        "active_tab": "session-a", "requested_session_ref": "session-b"}
            note("routing test: session_ref=session-b, active tab=session-a; "
                 f"notice on session-b tab={on_b}, on session-a tab={on_a}")
            check("A6-01 session_ref routing is NOT implemented (falls back to active tab)",
                  on_a >= 1 and on_b == 0, json.dumps(EVIDENCE["routing_test"]))
            await page2.close()

            # A7 reconnect after Core restart
            stop_core(core)
            await asyncio.sleep(2.0)
            st_down = await sw.evaluate("() => __cf.getState()")
            EVIDENCE["ws_after_core_stop"] = st_down.get("wsStatus")
            check("A7-01 extension notices Core disconnect", st_down.get("wsStatus") != "connected", st_down.get("wsStatus"))
            core = start_core()
            ws2 = await wait_ws(sw, "connected", timeout=45)
            EVIDENCE["ws_after_core_restart"] = (ws2 or {}).get("wsStatus")
            check("A7-02 extension reconnects after Core restart", bool(ws2), json.dumps(ws2)[:200] if ws2 else "no reconnect")

            # A8 fake clipboard injection
            pm = await ctx.new_page()
            await pm.goto(PM_LIKE, wait_until="domcontentloaded")
            await pm.wait_for_selector("#editor")
            await asyncio.sleep(0.5)
            tab_id = await sw.evaluate("""async () => {
                const tabs = await chrome.tabs.query({});
                const t = tabs.find(t => (t.url||'').includes('prosemirror-like'));
                return t ? t.id : null;
            }""")
            res = await sw.evaluate("""async (tabId) => await chrome.tabs.sendMessage(tabId, {type:'cf:fake_paste', text:'audit fake paste'})""", tab_id)
            EVIDENCE["fake_paste_result"] = res
            check("A8-01 fake-clipboard injection into ProseMirror-like editor", bool(res and res.get("ok") and "audit fake paste" in (res.get("text") or "")), json.dumps(res, ensure_ascii=False))
            await pm.close()

            # A9 content script on real chatgpt.com origin (public challenge page, no login)
            try:
                cp = await ctx.new_page()
                resp = await cp.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(4)
                st9 = await tab_state(sw, "chatgpt.com")
                EVIDENCE["chatgpt_public"] = {"status": resp.status if resp else None, "url": cp.url, "page_state": st9}
                check("A9-01 content script runs on chatgpt.com origin", bool(st9) and st9.get("kind") == "chatgpt", json.dumps(st9)[:300] if st9 else "no report")
                check("A9-02 session ref is none on the public challenge page", bool(st9) and (st9.get("session") or {}).get("kind") == "none", json.dumps((st9 or {}).get("session")))
                await cp.close()
            except Exception as exc:  # noqa: BLE001
                EVIDENCE["chatgpt_public_error"] = repr(exc)
                check("A9-01 content script runs on chatgpt.com origin", False, repr(exc))

            passed = sum(1 for c in EVIDENCE["checks"] if c["ok"])
            EVIDENCE["summary"] = {"passed": passed, "total": len(EVIDENCE["checks"])}
            EVIDENCE["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            (ARTIFACTS / "audit-repro-evidence.json").write_text(json.dumps(EVIDENCE, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"\n{passed}/{len(EVIDENCE['checks'])} checks passed")
            await ctx.close()
            return 0 if passed == len(EVIDENCE["checks"]) else 1
    finally:
        stop_core(core)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
