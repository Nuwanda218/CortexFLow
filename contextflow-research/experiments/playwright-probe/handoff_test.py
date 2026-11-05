#!/usr/bin/env python3
"""CF-EXP-002C-1: user-confirmed clipboard handoff prototype test.

Verifies the minimal loop:
  Core worker_completed -> extension side panel shows "待处理"
  -> user clicks "复制汇报" -> clipboard contains formatted Markdown
  -> user marks "已确认交接" -> status becomes confirmed (no auto-send anywhere).

Requires: mock-chat server on 8787 and Core on 8765 (started by the caller).
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

import requests
from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
EXT = HERE.parent / "edge-extension-probe"
ARTIFACTS = HERE.parent.parent / "_artifacts"
PROFILE = ARTIFACTS / "edge-profile-handoff"
CORE = "http://127.0.0.1:8765"
TOKEN = "dev-token-cf-exp-001"

EVIDENCE: dict = {"checks": []}


def check(name, ok, detail=""):
    EVIDENCE["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail else ""))


async def main() -> int:
    shutil.rmtree(PROFILE, ignore_errors=True)
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(PROFILE), channel="msedge", headless=True,
            args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}",
                  "--no-first-run", "--no-default-browser-check"],
        )
        sw = ctx.service_workers[0] if ctx.service_workers else await ctx.wait_for_event("serviceworker", timeout=15000)
        ext_id = sw.url.split("/")[2]
        EVIDENCE["extension_id"] = ext_id

        panel = await ctx.new_page()
        await panel.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="domcontentloaded")
        await panel.wait_for_function("() => !!window.__cfHandoff", timeout=10000)

        # Wait for the extension's WebSocket bridge to connect to Core.
        deadline = time.time() + 20
        connected = False
        while time.time() < deadline:
            st = await sw.evaluate("() => __cf.getState()")
            if st.get("wsStatus") == "connected":
                connected = True
                break
            await asyncio.sleep(0.5)
        check("C1-01 extension connected to local Core", connected)

        # Core pushes a worker_completed event.
        r = requests.post(f"{CORE}/api/notify",
                          json={"task_id": "task-handoff-001", "summary": "Codex 已完成 API 错误处理检查，发现 3 个问题。"},
                          headers={"X-CF-Token": TOKEN}, timeout=5).json()
        check("C1-02 Core accepted worker_completed push", r.get("delivered") == 1, json.dumps(r))

        # Panel should show a pending report (user-confirmed flow, no auto-send).
        await panel.wait_for_function(
            "() => window.__cfHandoff.getReports().some(r => r.task_id === 'task-handoff-001')", timeout=15000)
        reports = await panel.evaluate("() => window.__cfHandoff.getReports()")
        EVIDENCE["reports_after_push"] = reports
        check("C1-03 panel shows pending report", reports and reports[0]["status"] == "pending", json.dumps(reports))

        # User clicks "复制汇报".
        await panel.click('button[data-action="copy"]')
        await asyncio.sleep(0.5)
        copied = await panel.evaluate("() => window.__cfHandoff.getLastCopied()")
        EVIDENCE["copied_text"] = copied
        check("C1-04 copy button wrote formatted Markdown", bool(copied) and "Worker 汇报" in copied and "task-handoff-001" in copied,
              (copied or "")[:200])

        # Read the real clipboard to confirm it matches (normalize newlines).
        try:
            clip = await panel.evaluate("() => navigator.clipboard.readText()")
            EVIDENCE["clipboard_text"] = clip
            norm = lambda s: (s or "").replace("\r\n", "\n").strip()
            ok = norm(clip) == norm(copied) or ("task-handoff-001" in (clip or "") and "Worker" in (clip or ""))
            check("C1-05 system clipboard contains the report", ok,
                  f"copied_len={len(copied or '')} clip_len={len(clip or '')}")
        except Exception as exc:  # noqa: BLE001
            EVIDENCE["clipboard_error"] = repr(exc)
            check("C1-05 system clipboard contains the report", False, repr(exc))

        # Status transitions: copied -> confirmed.
        await panel.click('button[data-action="confirm"]')
        await asyncio.sleep(0.3)
        reports2 = await panel.evaluate("() => window.__cfHandoff.getReports()")
        EVIDENCE["reports_after_confirm"] = reports2
        check("C1-06 user confirmation updates status (copied -> confirmed)", reports2[0]["status"] == "confirmed", json.dumps(reports2))

        # No message was sent to any ChatGPT page (no chatgpt tab exists in this profile).
        pages = [pg.url for pg in ctx.pages]
        EVIDENCE["pages"] = pages
        check("C1-07 no ChatGPT page was touched / no auto-send", not any("chatgpt.com" in u for u in pages), json.dumps(pages))

        passed = sum(1 for c in EVIDENCE["checks"] if c["ok"])
        EVIDENCE["summary"] = {"passed": passed, "total": len(EVIDENCE["checks"])}
        (ARTIFACTS / "handoff-evidence.json").write_text(json.dumps(EVIDENCE, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n{passed}/{len(EVIDENCE['checks'])} checks passed")
        await ctx.close()
        return 0 if passed == len(EVIDENCE["checks"]) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
