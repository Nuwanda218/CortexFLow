#!/usr/bin/env python3
"""Focused debug for the MAIN-world bridge (B-04b)."""
import asyncio, json, shutil
from pathlib import Path
from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
EXT = HERE.parent / "edge-extension-probe"
PROFILE = HERE.parent.parent / "_artifacts" / "edge-profile-debug"

async def main():
    shutil.rmtree(PROFILE, ignore_errors=True)
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(PROFILE), channel="msedge", headless=True,
            args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}", "--no-first-run", "--no-default-browser-check"],
        )
        sw = ctx.service_workers[0] if ctx.service_workers else await ctx.wait_for_event("serviceworker", timeout=15000)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        page.on("console", lambda m: print("PAGE-CONSOLE:", m.type, m.text))
        page.on("pageerror", lambda e: print("PAGE-ERROR:", e))
        await page.goto("http://127.0.0.1:8787/mock/chat/session-a", wait_until="domcontentloaded")
        await asyncio.sleep(3)
        print("main-world __mockChat:", await page.evaluate("() => typeof window.__mockChat"))
        print("script tags:", await page.evaluate("() => Array.from(document.scripts).map(s => (s.src || s.textContent.slice(0,60)))"))
        # Does an event dispatched from the page reach the content script?
        await page.evaluate("""() => document.dispatchEvent(new CustomEvent('cf:main-world-probe', {detail:{payload:{version:'x',sessionId:'manual'}, error:null, at:'manual'}}))""")
        await asyncio.sleep(1.0)
        st = await sw.evaluate("() => __cf.getState()")
        tab = list(st["tabs"].values())[0]
        print("isolation after manual dispatch:", json.dumps(tab.get("isolation"), indent=1))
        await ctx.close()

asyncio.run(main())
