#!/usr/bin/env python3
"""CF-DEV-002 automated test (stages A-D) against a ChatGPT-like mock.

The mock page mimics the verified ChatGPT DOM contract and is served on
127.0.0.1:8892, which the extension now matches (content.js injects for real,
with a real chrome.runtime context — unlike the CF-DEV-001 init-script trick).

Simulated scenario per "tab": each mock page has its own session id baked into
the URL path (/c/<uuid>), and a button to switch session (SPA-style pushState).

Requires: Edge + Playwright. Starts bridge_server.py on 8766 itself.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
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
PROFILE = ARTIFACTS / "edge-profile-cfdev002"
MOCK_DIR = ARTIFACTS / "cfdev002-mock"
PORT = 8892
BRIDGE = "ws://127.0.0.1:8766/ws?token=dev-token-cf-exp-002"
ORIGIN = "http://127.0.0.1:8766"

SESS_A = "aaaa1111-0000-4000-8000-00000000000a"
SESS_B = "bbbb2222-0000-4000-8000-00000000000b"

MOCK_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>ChatGPT mock</title></head>
<body>
<main>
  <div id="turns"></div>
  <div data-talvt-turn-state="complete" hidden></div>
</main>
<button id="switch" onclick="switchSession()">switch session</button>
<script>
  const SESS = new URLSearchParams(location.search).get("s") || "SESSION";
  function render() {
    document.getElementById("turns").innerHTML = `
      <div data-chatgpt-search-unit-key="fallback-turn-0:0:user"
           data-chatgpt-search-message-ids="11111111-0000-4000-8000-000000000001">
        <h4 data-conversation-role="user"></h4>
        <div data-user-message-bubble="true"><div data-d-component="text">mock msg user ${SESS.slice(0,4)}</div></div>
      </div>
      <div data-chatgpt-search-unit-key="fallback-turn-0:1:assistant"
           data-chatgpt-search-message-ids="22222222-0000-4000-8000-000000000002">
        <h4 data-conversation-role="assistant"></h4>
        <div data-dil-message-id="22222222-0000-4000-8000-000000000002">
          <div data-d-component="text">mock reply for ${SESS.slice(0,4)} (count=${window.__renderCount || 0})</div>
        </div>
      </div>`;
  }
  window.__renderCount = 0;
  render();
  function switchSession() {
    const next = SESS === "AAAA" ? "BBBB" : "AAAA";
    history.pushState({}, "", "/c/" + (next === "AAAA" ? "aaaa1111-0000-4000-8000-00000000000a" : "bbbb2222-0000-4000-8000-00000000000b"));
    window.__renderCount += 1;
    render();
  }
</script>
</body></html>"""

EVIDENCE: dict = {"checks": []}


def check(name, ok, detail=""):
    EVIDENCE["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(("PASS " if ok else "FAIL ") + name + (f" :: {str(detail)[:200]}" if detail else ""), flush=True)


async def ws_call(payload: dict, expect_id: str) -> dict:
    async with websockets.connect(BRIDGE, origin=ORIGIN) as ws:
        await ws.send(json.dumps(payload))
        while True:
            resp = json.loads(await ws.recv())
            if resp.get("type") == "welcome":
                continue
            if resp.get("id") == expect_id:
                return resp


async def main() -> int:
    shutil.rmtree(PROFILE, ignore_errors=True)
    MOCK_DIR.mkdir(parents=True, exist_ok=True)
    (MOCK_DIR / "chat.html").write_text(MOCK_PAGE, encoding="utf-8")

    # ThreadingHTTPServer with default bind fails under some asyncio loops;
    # bind explicitly and suppress noisy logging.
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    handler = partial(QuietHandler, directory=str(MOCK_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    server.daemon_threads = True
    server_thread = asyncio.get_running_loop().run_in_executor(None, server.serve_forever)

    bridge = subprocess.Popen([sys.executable, str(EXT / "bridge_server.py"), "--port", "8766"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):
            try:
                if requests.get("http://127.0.0.1:8766/health", timeout=1).status_code == 200:
                    break
            except Exception:
                time.sleep(0.3)

        async with async_playwright() as p:
            ctx = await p.chromium.launch_persistent_context(
                str(PROFILE), channel="msedge", headless=True,
                args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}",
                      "--no-first-run", "--no-default-browser-check"],
            )
            sw = ctx.service_workers[0] if ctx.service_workers else await ctx.wait_for_event("serviceworker", timeout=20000)
            # Two mock "ChatGPT" tabs with different sessions (real extension context!)
            pageA = await ctx.new_page()
            await pageA.goto(f"http://127.0.0.1:{PORT}/chat.html?s=AAAA", wait_until="domcontentloaded")
            await pageA.evaluate("history.pushState({}, '', '/c/aaaa1111-0000-4000-8000-00000000000a')")
            # __cfAdapter lives in the content script's ISOLATED world; Playwright's
            # page.evaluate runs in MAIN world and cannot see it. Just wait via the
            # console logs side effect: use a DOM marker instead — the content
            # script logs; simplest robust wait is polling the SW's tabs state.
            pageA_id = None
            for _ in range(60):
                tabs = await sw.evaluate("() => __cfBg.getState().tabs")
                entry = next((t for t in tabs.values() if "aaaa1111" in (t.get("url") or "")), None)
                if entry and entry.get("message_count", 0) >= 2:
                    pageA_id = entry["tabId"]
                    break
                await asyncio.sleep(0.4)
            check("T0a tab A injected & reported", pageA_id is not None)
            pageB = await ctx.new_page()
            await pageB.goto(f"http://127.0.0.1:{PORT}/chat.html?s=BBBB", wait_until="domcontentloaded")
            await pageB.evaluate("history.pushState({}, '', '/c/bbbb2222-0000-4000-8000-00000000000b')")
            pageB_ok = False
            for _ in range(40):
                tabs = await sw.evaluate("() => __cfBg.getState().tabs")
                entry = next((t for t in tabs.values() if "bbbb2222" in (t.get("url") or "")), None)
                if entry and entry.get("message_count", 0) >= 2:
                    pageB_ok = True
                    break
                await asyncio.sleep(0.4)
            check("T0b tab B injected & reported", pageB_ok)

            # Bridge client page (real relay to extension)
            pageC = await ctx.new_page()
            await pageC.goto("http://127.0.0.1:8766/bridge_client.html?token=dev-token-cf-exp-002",
                             wait_until="domcontentloaded")
            await pageC.wait_for_function("() => window.__cfBridgePage && window.__cfBridgePage.isConnected()",
                                          timeout=15000)
            check("T0 bridge page connected through extension relay", True)

            # T1 list_sessions shows both tabs (fresh probes, not cache)
            r1 = await ws_call({"id": "req-1", "type": "list_sessions"}, "req-1")
            ready_sids = sorted([s.get("session_id") for s in r1["data"]["sessions"] if s.get("ready") and s.get("session_id")])
            EVIDENCE["list"] = r1["data"]
            check("T1 list_sessions finds both sessions", ready_sids == [SESS_A, SESS_B], json.dumps(ready_sids))

            # T2 get_snapshot routes to the right tab
            r2 = await ws_call({"id": "req-2", "type": "get_snapshot", "session_id": SESS_A}, "req-2")
            snapA = r2.get("data", {}).get("snapshot", {})
            check("T2 snapshot session_id matches", (snapA.get("session") or {}).get("session_id") == SESS_A,
                  json.dumps((snapA.get("session") or {})))
            check("T2b snapshot has messages", len(snapA.get("messages") or []) == 2, str(len(snapA.get("messages") or [])))

            # T3 realtime, not cache: change page content between two reads
            await pageA.evaluate("window.__renderCount = 42; document.querySelector('[data-dil-message-id] [data-d-component=text]').innerText = 'updated live content 42'")
            r3 = await ws_call({"id": "req-3", "type": "get_snapshot", "session_id": SESS_A}, "req-3")
            texts = " ".join(m.get("content", "") for m in (r3.get("data", {}).get("snapshot", {}).get("messages") or []))
            check("T3 realtime read returns updated content", "updated live content 42" in texts, texts[:150])

            # T4 session not found
            r4 = await ws_call({"id": "req-4", "type": "get_snapshot", "session_id": "nope-1234"}, "req-4")
            check("T4 SESSION_NOT_FOUND", not r4["success"] and r4["error"]["code"] == "SESSION_NOT_FOUND", json.dumps(r4.get("error")))

            # T5 invalid request
            r5 = await ws_call({"id": "req-5", "type": "get_snapshot"}, "req-5")
            check("T5 INVALID_REQUEST (no session_id/tab_id)", not r5["success"] and r5["error"]["code"] == "INVALID_REQUEST", json.dumps(r5.get("error")))

            # T6 SPA navigation mid-life: switch tab A to session B, then request old session
            await pageA.click("#switch")
            await pageA.wait_for_url("**/c/bbbb2222-0000-4000-8000-00000000000b", timeout=5000)
            r6 = await ws_call({"id": "req-6", "type": "get_snapshot", "session_id": SESS_A}, "req-6")
            check("T6 SESSION_NOT_FOUND after SPA switch", not r6["success"] and r6["error"]["code"] == "SESSION_NOT_FOUND", json.dumps(r6.get("error")))

            # T7 now two tabs show session B -> AMBIGUOUS
            r7 = await ws_call({"id": "req-7", "type": "get_snapshot", "session_id": SESS_B}, "req-7")
            check("T7 AMBIGUOUS_SESSION", not r7["success"] and r7["error"]["code"] == "AMBIGUOUS_SESSION", json.dumps(r7.get("error")))

            # T8 disambiguate with tab_id (both tabs report session B now)
            tabs_state = await sw.evaluate("() => __cfBg.getState().tabs")
            tab_b_list = [int(t["tabId"]) for t in tabs_state.values() if "bbbb2222" in (t.get("url") or "")]
            r8 = await ws_call({"id": "req-8", "type": "get_snapshot", "session_id": SESS_B, "tab_id": tab_b_list[0]}, "req-8")
            check("T8 tab_id disambiguates", r8["success"] and (r8["data"]["snapshot"]["session"]["session_id"] == SESS_B), json.dumps(r8.get("error") or {"tab": r8.get("data", {}).get("tab_id")}))

            # T9 invalid JSON — first frame is the welcome; skip it
            async with websockets.connect(BRIDGE, origin=ORIGIN) as ws:
                await ws.send("not json at all")
                resp = None
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") == "welcome":
                        continue
                    resp = msg
                    break
                check("T9 invalid JSON -> INVALID_REQUEST", (resp or {}).get("error", {}).get("code") == "INVALID_REQUEST", json.dumps((resp or {}).get("error")))

            # T10 unknown type
            r10 = await ws_call({"id": "req-10", "type": "send_message"}, "req-10")
            check("T10 unknown type rejected", not r10["success"] and r10["error"]["code"] == "INVALID_REQUEST", json.dumps(r10.get("error")))

            passed = sum(1 for c in EVIDENCE["checks"] if c["ok"])
            EVIDENCE["summary"] = {"passed": passed, "total": len(EVIDENCE["checks"])}
            (ARTIFACTS / "cfdev002-evidence.json").write_text(json.dumps(EVIDENCE, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"\n{passed}/{len(EVIDENCE['checks'])} checks passed")
            await ctx.close()
            return 0 if passed == len(EVIDENCE["checks"]) else 1
    finally:
        server.shutdown()
        bridge.terminate()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
