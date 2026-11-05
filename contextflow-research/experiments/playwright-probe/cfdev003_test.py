#!/usr/bin/env python3
"""CF-DEV-003 automated test: wait_for_reply on a ChatGPT-like mock.

Covers the Coordinator's required traps:
  T1 stale-completed: page already completed at baseline -> must NOT return old reply
  T2 normal: new reply arrives -> returned with new message id
  T3 timeout -> TIMEOUT with last state
  T4 session change during wait -> SESSION_CHANGED
  T5 in_progress is not complete -> keeps waiting, returns after finish
  T6 regenerate -> new message id returned
  T7 multiple new replies -> returns last + multiple flag
  T8 tab closed -> SESSION_NOT_FOUND
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests
from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
EXT = ROOT / "contextflow-extension"
ARTIFACTS = ROOT / "_artifacts"
MOCK_DIR = ARTIFACTS / "cfdev003-mock"
PROFILE = ARTIFACTS / "edge-profile-cfdev003"
PORT = 8893
SESS_A = "aaaa1111-0000-4000-8000-00000000000a"
SESS_B = "bbbb2222-0000-4000-8000-00000000000b"

sys.path.insert(0, str(HERE))
from cfdev003_wait import BridgeClient, wait_for_reply  # noqa: E402

EVIDENCE: dict = {"checks": []}


def check(name, ok, detail=""):
    EVIDENCE["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)[:400]})
    print(("PASS " if ok else "FAIL ") + name + (f" :: {str(detail)[:200]}" if detail else ""), flush=True)


async def main() -> int:
    shutil.rmtree(PROFILE, ignore_errors=True)
    MOCK_DIR.mkdir(parents=True, exist_ok=True)
    (MOCK_DIR / "mock.html").write_text((ARTIFACTS / "cfdev003-mock" / "mock.html").read_text(encoding="utf-8"), encoding="utf-8")

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", PORT), partial(Quiet, directory=str(MOCK_DIR)))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()

    bridge = subprocess.Popen([sys.executable, str(EXT / "bridge_server.py"), "--port", "8766"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
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

            page = await ctx.new_page()
            await page.goto(f"http://127.0.0.1:{PORT}/mock.html?s={SESS_A}", wait_until="domcontentloaded")
            # wait until content script reported this session
            for _ in range(50):
                tabs = await sw.evaluate("() => __cfBg.getState().tabs")
                if any(SESS_A in (t.get("url") or "") and t.get("message_count", 0) >= 0 for t in tabs.values()):
                    break
                await asyncio.sleep(0.3)

            bridge_page = await ctx.new_page()
            await bridge_page.goto("http://127.0.0.1:8766/bridge_client.html?token=dev-token-cf-exp-002",
                                   wait_until="domcontentloaded")
            await bridge_page.wait_for_function("() => window.__cfBridgePage && window.__cfBridgePage.isConnected()", timeout=15000)

            client = BridgeClient()
            await client.connect()
            try:
                # ---------- T1 stale-completed trap ----------
                await page.evaluate("() => window.__mock.seedCompleted()")
                await asyncio.sleep(0.8)
                task = asyncio.create_task(wait_for_reply(client, SESS_A, timeout=20, poll=0.4))
                await asyncio.sleep(2.0)
                check("T1 stale-completed: no premature return", not task.done(), "still waiting after 2s")
                await page.evaluate("() => { window.__mock.sendUser('T1 user'); window.__mock.startReply(); window.__mock.appendText('T1 fresh reply'); window.__mock.finishReply(); }")
                r1 = await asyncio.wait_for(task, timeout=20)
                check("T1 returns the NEW reply, not the old one",
                      r1.get("ok") and "T1 fresh reply" in (r1["reply"]["content"] or "") and "OLD" not in (r1["reply"]["content"] or ""),
                      json.dumps(r1.get("reply", {}).get("content", ""))[:120])

                # ---------- T2 normal ----------
                await page.evaluate("() => window.__mock.reset()")
                await asyncio.sleep(0.6)
                base = await client.get_snapshot(SESS_A)
                task2 = asyncio.create_task(wait_for_reply(client, SESS_A, timeout=20, poll=0.4, baseline=base))
                await asyncio.sleep(0.6)
                await page.evaluate("() => { window.__mock.sendUser('T2 user'); window.__mock.startReply(); window.__mock.appendText('T2 reply body'); window.__mock.finishReply(); }")
                r2 = await asyncio.wait_for(task2, timeout=20)
                check("T2 normal wait returns reply", r2.get("ok") and "T2 reply body" in (r2["reply"]["content"] or ""),
                      json.dumps(r2.get("reply", {}).get("content", ""))[:120])
                check("T2 reply id is not in baseline",
                      r2.get("ok") and r2["reply"]["message_id"] not in {i for m in base["messages"] for i in (m.get("message_ids") or [])},
                      str(r2.get("reply", {}).get("message_id")))

                # ---------- T3 timeout ----------
                await page.evaluate("() => window.__mock.reset()")
                await asyncio.sleep(0.6)
                r3 = await wait_for_reply(client, SESS_A, timeout=2.0, poll=0.4)
                check("T3 timeout returns TIMEOUT with last state",
                      not r3.get("ok") and r3["error"]["code"] == "TIMEOUT" and "last_state" in r3,
                      json.dumps(r3.get("error")))

                # ---------- T4 session change ----------
                await page.evaluate("() => window.__mock.reset()")
                await asyncio.sleep(0.6)
                task4 = asyncio.create_task(wait_for_reply(client, SESS_A, timeout=20, poll=0.4))
                await asyncio.sleep(1.0)
                await page.evaluate(f"() => window.__mock.switchSession('{SESS_B}')")
                r4 = await asyncio.wait_for(task4, timeout=20)
                check("T4 session change -> SESSION_CHANGED",
                      not r4.get("ok") and r4["error"]["code"] == "SESSION_CHANGED",
                      json.dumps(r4.get("error")))

                # back to session A for remaining tests
                await page.evaluate(f"() => window.__mock.switchSession('{SESS_A}')")
                await asyncio.sleep(1.0)

                # ---------- T5 in_progress is not complete ----------
                await page.evaluate("() => window.__mock.reset()")
                await asyncio.sleep(0.6)
                task5 = asyncio.create_task(wait_for_reply(client, SESS_A, timeout=20, poll=0.4))
                await asyncio.sleep(0.6)
                await page.evaluate("() => { window.__mock.sendUser('T5 user'); window.__mock.startReply(); window.__mock.appendText('T5 partial'); }")
                await asyncio.sleep(2.0)
                check("T5 in_progress not treated as complete", not task5.done(), "still waiting during in_progress")
                await page.evaluate("() => { window.__mock.appendText(' + finished'); window.__mock.finishReply(); }")
                r5 = await asyncio.wait_for(task5, timeout=20)
                check("T5 returns after completion", r5.get("ok") and "T5 partial + finished" in (r5["reply"]["content"] or ""),
                      json.dumps(r5.get("reply", {}).get("content", ""))[:120])

                # ---------- T6 regenerate ----------
                await page.evaluate("() => window.__mock.reset()")
                await asyncio.sleep(0.6)
                base6 = await client.get_snapshot(SESS_A)
                task6 = asyncio.create_task(wait_for_reply(client, SESS_A, timeout=20, poll=0.4, baseline=base6))
                await asyncio.sleep(0.6)
                await page.evaluate("() => { window.__mock.sendUser('T6 user'); window.__mock.startReply(); window.__mock.appendText('T6 first answer'); window.__mock.finishReply(); }")
                r6a = await asyncio.wait_for(task6, timeout=20)
                first_id = r6a["reply"]["message_id"] if r6a.get("ok") else None
                base6b = await client.get_snapshot(SESS_A)
                task6b = asyncio.create_task(wait_for_reply(client, SESS_A, timeout=20, poll=0.4, baseline=base6b))
                await asyncio.sleep(0.6)
                await page.evaluate("() => { window.__mock.regenerate(); window.__mock.appendText('T6 regenerated answer'); window.__mock.finishReply(); }")
                r6b = await asyncio.wait_for(task6b, timeout=20)
                check("T6 regenerate returns a NEW message id",
                      r6b.get("ok") and r6b["reply"]["message_id"] != first_id and "regenerated" in (r6b["reply"]["content"] or ""),
                      f"first={first_id} regen={r6b.get('reply', {}).get('message_id')}")

                # ---------- T7 multiple new replies ----------
                await page.evaluate("() => window.__mock.reset()")
                await asyncio.sleep(0.6)
                task7 = asyncio.create_task(wait_for_reply(client, SESS_A, timeout=20, poll=0.4))
                await asyncio.sleep(0.6)
                await page.evaluate("""() => {
                    window.__mock.sendUser('T7 user 1'); window.__mock.startReply();
                    window.__mock.appendText('T7 answer 1'); window.__mock.finishReply();
                    window.__mock.sendUser('T7 user 2'); window.__mock.startReply();
                    window.__mock.appendText('T7 answer 2'); window.__mock.finishReply();
                }""")
                r7 = await asyncio.wait_for(task7, timeout=20)
                check("T7 multiple replies -> last one + multiple flag",
                      r7.get("ok") and "T7 answer 2" in (r7["reply"]["content"] or "") and r7.get("multiple") is True,
                      f"multiple={r7.get('multiple')} ids={r7.get('new_assistant_ids')}")

                # ---------- T8 tab closed ----------
                await page.close()
                await asyncio.sleep(1.0)
                r8 = await wait_for_reply(client, SESS_A, timeout=3.0, poll=0.4)
                check("T8 tab closed -> SESSION_NOT_FOUND",
                      not r8.get("ok") and r8["error"]["code"] in ("SESSION_NOT_FOUND", "CONTENT_SCRIPT_UNAVAILABLE"),
                      json.dumps(r8.get("error")))
            finally:
                await client.close()

            passed = sum(1 for c in EVIDENCE["checks"] if c["ok"])
            EVIDENCE["summary"] = {"passed": passed, "total": len(EVIDENCE["checks"])}
            (ARTIFACTS / "cfdev003-evidence.json").write_text(json.dumps(EVIDENCE, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"\n{passed}/{len(EVIDENCE['checks'])} checks passed")
            await ctx.close()
            return 0 if passed == len(EVIDENCE["checks"]) else 1
    finally:
        server.shutdown()
        bridge.terminate()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
