#!/usr/bin/env python3
"""CF-EXP-002D: "fake clipboard" cross-world injection test.

Question: can a content script in the ISOLATED world inject text into a page's
ProseMirror-like editor by dispatching a synthetic ClipboardEvent('paste') with a
DataTransfer, WITHOUT touching the OS clipboard?

Uses the local prosemirror-like.html page (mimics ProseMirror's paste contract).
Requires mock-chat server on 8787.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
EXT = HERE.parent / "edge-extension-probe"
ARTIFACTS = HERE.parent.parent / "_artifacts"
PROFILE = ARTIFACTS / "edge-profile-fakeclip"
PAGE = "http://127.0.0.1:8787/prosemirror-like.html"

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
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(PAGE, wait_until="domcontentloaded")
        await page.wait_for_selector("#editor")

        tab_id = await sw.evaluate("""async () => {
            const tabs = await chrome.tabs.query({});
            const t = tabs.find(t => (t.url||'').includes('prosemirror-like'));
            return t ? t.id : null;
        }""")
        check("D1-01 test page loaded and tab found", tab_id is not None, f"tab={tab_id}")

        # Content script (isolated world) dispatches the synthetic paste.
        result = await sw.evaluate("""async (tabId) => {
            return await chrome.tabs.sendMessage(tabId, {type:'cf:fake_paste', text:'CF fake clipboard 789'});
        }""", tab_id)
        EVIDENCE["content_script_result"] = result
        check("D1-02 content script dispatched synthetic paste", bool(result and result.get("ok")), json.dumps(result, ensure_ascii=False))
        check("D1-03 page editor received the text", bool(result and "CF fake clipboard 789" in (result.get("text") or "")), json.dumps(result, ensure_ascii=False))

        # Page-side evidence: the paste event was untrusted but handled.
        page_events = await page.evaluate("() => window.__pasteEvents")
        EVIDENCE["page_paste_events"] = page_events
        check("D1-04 page recorded an untrusted paste event", bool(page_events) and page_events[0]["trusted"] is False,
              json.dumps(page_events, ensure_ascii=False))
        check("D1-05 page inserted text via clipboardData", bool(page_events) and page_events[0]["text"] == "CF fake clipboard 789",
              json.dumps(page_events, ensure_ascii=False))

        # Second injection to prove repeatability.
        result2 = await sw.evaluate("""async (tabId) => {
            return await chrome.tabs.sendMessage(tabId, {type:'cf:fake_paste', text:' second'});
        }""", tab_id)
        text = await page.evaluate("() => window.__editorText()")
        EVIDENCE["text_after_second"] = text
        check("D1-06 repeated injection works", "second" in text, text[:120])

        passed = sum(1 for c in EVIDENCE["checks"] if c["ok"])
        EVIDENCE["summary"] = {"passed": passed, "total": len(EVIDENCE["checks"])}
        (ARTIFACTS / "fake-clipboard-evidence.json").write_text(json.dumps(EVIDENCE, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n{passed}/{len(EVIDENCE['checks'])} checks passed")
        await ctx.close()
        return 0 if passed == len(EVIDENCE["checks"]) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
