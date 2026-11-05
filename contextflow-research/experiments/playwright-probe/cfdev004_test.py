#!/usr/bin/env python3
"""CF-DEV-004 experiment A automated test: prepare_message (fill only, never send).

Covers:
  T1  read_composer on empty composer
  T2  prepare into empty composer -> verified
  T3  read back matches prepared text
  T4  existing draft -> DRAFT_PRESENT, composer untouched
  T5  force=true replaces draft
  T6  multiline + code block preserved
  T7  special chars / emoji preserved
  T8  NO SEND: message count unchanged after prepare
  T9  composer missing -> COMPOSER_UNAVAILABLE
  T10 clear_composer empties the composer
  T11 unknown session -> SESSION_NOT_FOUND
  T12 empty text -> INVALID_REQUEST
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
MOCK_DIR = ARTIFACTS / "cfdev004-mock"
PROFILE = ARTIFACTS / "edge-profile-cfdev004"
PORT = 8898
SESS_A = "aaaa1111-0000-4000-8000-00000000000a"

sys.path.insert(0, str(HERE))
from cfdev003_wait import BridgeClient, BridgeError  # noqa: E402
from cfdev004_prepare import get_composer, prepare_message, clear_composer  # noqa: E402

EVIDENCE: dict = {"checks": []}

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def check(name, ok, detail=""):
    EVIDENCE["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)[:400]})
    print(("PASS " if ok else "FAIL ") + name + (f" :: {str(detail)[:200]}" if detail else ""), flush=True)


async def expect_error(coro, code):
    try:
        await coro
        return None
    except BridgeError as exc:
        return exc


async def main() -> int:
    shutil.rmtree(PROFILE, ignore_errors=True)

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
            page = await ctx.new_page()
            await page.goto(f"http://127.0.0.1:{PORT}/mock.html?s={SESS_A}", wait_until="domcontentloaded")
            await asyncio.sleep(2.0)

            bp = await ctx.new_page()
            await bp.goto("http://127.0.0.1:8766/bridge_client.html?token=dev-token-cf-exp-002",
                          wait_until="domcontentloaded")
            await bp.wait_for_function("() => window.__cfBridgePage && window.__cfBridgePage.isConnected()", timeout=15000)

            client = BridgeClient()
            await client.connect()
            try:
                # T1 empty composer
                c1 = await get_composer(client, SESS_A)
                comp = c1["composer"]
                check("T1 read_composer empty", comp["found"] and comp["empty"] and comp["editable"],
                      json.dumps(comp, ensure_ascii=False))

                # T2/T3 prepare + read back
                r2 = await prepare_message(client, SESS_A, "Hello ContextFlow")
                check("T2 prepare verified", r2.get("prepared") and r2.get("verified") and r2.get("sent") is False,
                      json.dumps(r2, ensure_ascii=False))
                c3 = await get_composer(client, SESS_A)
                check("T3 read back matches", c3["composer"]["text"].strip() == "Hello ContextFlow",
                      repr(c3["composer"]["text"]))

                # T4 draft present -> refuse
                await page.evaluate("() => window.__mock.setDraft('user draft text')")
                await asyncio.sleep(0.3)
                err4 = await expect_error(prepare_message(client, SESS_A, "should not overwrite"), "DRAFT_PRESENT")
                c4 = await get_composer(client, SESS_A)
                check("T4 draft present -> DRAFT_PRESENT, untouched",
                      err4 is not None and err4.code == "DRAFT_PRESENT" and "user draft text" in c4["composer"]["text"],
                      f"{getattr(err4, 'code', None)} / {c4['composer']['text']!r}")

                # T5 force replaces
                r5 = await prepare_message(client, SESS_A, "forced replacement", force=True)
                check("T5 force replaces draft", r5.get("verified") and r5.get("draft_replaced") is True,
                      json.dumps(r5, ensure_ascii=False))

                # T6 multiline + code block
                multi = "line one\nline two\n\n```python\nprint('hi')\n```\nlast line"
                r6 = await prepare_message(client, SESS_A, multi, force=True)
                c6 = await get_composer(client, SESS_A)
                check("T6 multiline preserved", r6.get("verified") and "line one" in c6["composer"]["text"]
                      and "print('hi')" in c6["composer"]["text"] and "last line" in c6["composer"]["text"],
                      repr(c6["composer"]["text"])[:160])

                # T7 special chars / emoji
                special = "中文测试 ✅ 🚀 <tag> & \"quotes\" 'single' 100%"
                r7 = await prepare_message(client, SESS_A, special, force=True)
                c7 = await get_composer(client, SESS_A)
                check("T7 special chars preserved", r7.get("verified") and "🚀" in c7["composer"]["text"]
                      and "<tag>" in c7["composer"]["text"],
                      repr(c7["composer"]["text"])[:160])

                # T8 no send: message count unchanged
                snap = await client.get_snapshot(SESS_A)
                msgs = (snap.get("snapshot") or {}).get("messages") or []
                check("T8 no message sent", len(msgs) == 0, f"message_count={len(msgs)}")

                # T9 composer missing
                await page.evaluate("() => window.__mock.removeComposer()")
                await asyncio.sleep(0.3)
                c9 = await get_composer(client, SESS_A)
                err9 = await expect_error(prepare_message(client, SESS_A, "x"), "COMPOSER_UNAVAILABLE")
                check("T9 composer missing -> found=false + COMPOSER_UNAVAILABLE",
                      c9["composer"]["found"] is False and err9 is not None and err9.code == "COMPOSER_UNAVAILABLE",
                      f"found={c9['composer']['found']} err={getattr(err9, 'code', None)}")
                await page.evaluate("() => window.__mock.restoreComposer()")
                await asyncio.sleep(0.3)

                # T10 clear
                r10 = await clear_composer(client, SESS_A)
                check("T10 clear_composer empties", r10.get("cleared") is True, json.dumps(r10, ensure_ascii=False))

                # T11 unknown session
                err11 = await expect_error(get_composer(client, "nope-1234"), "SESSION_NOT_FOUND")
                check("T11 unknown session -> SESSION_NOT_FOUND", err11 is not None and err11.code == "SESSION_NOT_FOUND",
                      getattr(err11, "code", None))

                # T12 empty text
                err12 = await expect_error(prepare_message(client, SESS_A, ""), "INVALID_REQUEST")
                check("T12 empty text -> INVALID_REQUEST", err12 is not None and err12.code == "INVALID_REQUEST",
                      getattr(err12, "code", None))
            finally:
                await client.close()

            passed = sum(1 for c in EVIDENCE["checks"] if c["ok"])
            EVIDENCE["summary"] = {"passed": passed, "total": len(EVIDENCE["checks"])}
            (ARTIFACTS / "cfdev004-evidence.json").write_text(json.dumps(EVIDENCE, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"\n{passed}/{len(EVIDENCE['checks'])} checks passed")
            await ctx.close()
            return 0 if passed == len(EVIDENCE["checks"]) else 1
    finally:
        server.shutdown()
        bridge.terminate()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
