#!/usr/bin/env python3
"""Playwright generic capability probe (CF-EXP-001, task E2).

Answers, with executable evidence, whether Playwright can:
  E-01 launch the installed Microsoft Edge (channel="msedge") without downloading Chromium
  E-02 use an isolated user-data-dir profile
  E-03 open a specific mock session URL, type a message, wait for streaming completion
  E-04 extract the finished assistant message text (incl. code block / table)
  E-05 switch sessions and re-open a session URL
  E-06 reuse an existing Edge profile directory (copy) - what breaks
  E-07 connect over CDP to an Edge started with --remote-debugging-port
  E-08 multi-tab management
  E-09 conflict when a human and the automation act on the same page (simulated)

It never touches a real ChatGPT account. Run:
  python playwright_generic.py
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
ARTIFACTS = HERE.parent.parent / "_artifacts"
PROFILE = ARTIFACTS / "edge-profile-generic"
MOCK = "http://127.0.0.1:8787/mock/chat/session-a"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

EVIDENCE: dict = {"checks": [], "notes": []}


def check(name, ok, detail=""):
    EVIDENCE["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail else ""), flush=True)


async def wait_idle(page, timeout=15000):
    await page.wait_for_function("() => document.body.dataset.appState === 'idle'", timeout=timeout)


async def main() -> int:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(PROFILE, ignore_errors=True)
    async with async_playwright() as p:
        # E-01 / E-02: installed Edge + isolated profile
        ctx = await p.chromium.launch_persistent_context(
            str(PROFILE), channel="msedge", headless=True,
            viewport={"width": 1280, "height": 860},
            args=["--no-first-run", "--no-default-browser-check"],
        )
        EVIDENCE["browser_version"] = ctx.browser.version if ctx.browser else None
        check("E-01 launch installed Edge via channel=msedge", bool(ctx.browser), f"version={ctx.browser.version if ctx.browser else None}")
        check("E-02 isolated user-data-dir created", PROFILE.exists() and any(PROFILE.iterdir()), str(PROFILE))

        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(MOCK, wait_until="domcontentloaded")
        check("E-03a open specific session URL", page.url.endswith("/mock/chat/session-a"), page.url)

        await page.fill("#prompt-input", "Playwright typed this message.")
        await page.click("#send")
        await page.wait_for_function("() => document.body.dataset.appState === 'generating'", timeout=5000)
        t0 = time.time()
        await wait_idle(page)
        elapsed = time.time() - t0
        check("E-03b type + wait for streaming completion", True, f"streaming took {elapsed:.2f}s")

        # E-04: extract the last assistant message
        last = page.locator('.message[data-role="assistant"]').last
        text = await last.inner_text()
        status = await last.get_attribute("data-status")
        check("E-04a read finished assistant message", status == "complete" and "mock reply" in text, f"status={status} len={len(text)}")
        check("E-04b structured content preserved (code block / table)", "```python" in text and "| alpha |" in text, text[-120:].replace("\n", "\\n"))

        # E-05: session switch + re-open
        await page.click('a.session-link[data-session-id="session-b"]')
        await page.wait_for_function("() => document.body.dataset.sessionId === 'session-b'")
        check("E-05a switch session in SPA", page.url.endswith("/mock/chat/session-b"), page.url)
        await page.reload(wait_until="domcontentloaded")
        await page.wait_for_selector("#conversation .message")
        sid = await page.evaluate("() => document.body.dataset.sessionId")
        check("E-05b re-open session URL after reload", sid == "session-b", f"session={sid} url={page.url}")

        # E-08: multi-tab management
        tab2 = await ctx.new_page()
        await tab2.goto("http://127.0.0.1:8787/mock/chat/session-a", wait_until="domcontentloaded")
        check("E-08 multi-tab management", len(ctx.pages) >= 2, f"pages={len(ctx.pages)}")
        await tab2.close()

        # E-09: simulated human/automation conflict on the same page
        await page.fill("#prompt-input", "automation draft")
        await page.fill("#prompt-input", "human overwrote the draft")
        value = await page.input_value("#prompt-input")
        check("E-09 concurrent edit is last-writer-wins (no locking)", value == "human overwrote the draft", f"value={value!r}")

        await ctx.close()

        # E-06: reuse of the daily Edge profile.
        # SAFETY: we never point Playwright at the live profile directory (it is in
        # use by the running Edge and could be corrupted). We only measure its size
        # and test a *minimal skeleton* copy (no cookie/credential files).
        real = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data"
        if real.exists():
            total = 0
            for f in real.rglob("*"):
                try:
                    if f.is_file():
                        total += f.stat().st_size
                except OSError:
                    pass
            EVIDENCE["real_profile_size_mb"] = round(total / 1024 / 1024, 1)
            skel = ARTIFACTS / "edge-profile-skeleton"
            shutil.rmtree(skel, ignore_errors=True)
            skel.mkdir(parents=True, exist_ok=True)
            copied = []
            for rel in ["Local State", "Default/Preferences"]:
                src = real / rel
                if src.exists():
                    dst = skel / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    copied.append(rel)
            try:
                ctx2 = await p.chromium.launch_persistent_context(str(skel), channel="msedge", headless=True, args=["--no-first-run"])
                pg = ctx2.pages[0] if ctx2.pages else await ctx2.new_page()
                await pg.goto("about:blank")
                EVIDENCE["profile_skeleton"] = {"copied": copied, "launched": True}
                check("E-06 profile skeleton (no credentials) launches", True,
                      f"real profile={EVIDENCE['real_profile_size_mb']} MB, copied {copied} (cookie DB deliberately excluded)")
                await ctx2.close()
            except Exception as exc:  # noqa: BLE001
                EVIDENCE["profile_skeleton"] = {"copied": copied, "launched": False, "error": repr(exc)}
                check("E-06 profile skeleton (no credentials) launches", False, repr(exc))
            finally:
                shutil.rmtree(skel, ignore_errors=True)
        else:
            check("E-06 profile reuse", False, "no Edge User Data dir")

        # E-07: connect over CDP to a separately launched Edge
        cdp_profile = ARTIFACTS / "edge-profile-cdp"
        shutil.rmtree(cdp_profile, ignore_errors=True)
        proc = subprocess.Popen([
            EDGE, "--headless=new", "--remote-debugging-port=9333",
            f"--user-data-dir={cdp_profile}", "--no-first-run", "--no-default-browser-check",
            "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.time() + 20
            connected = False
            while time.time() < deadline:
                try:
                    browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9333")
                    connected = True
                    break
                except Exception:
                    await asyncio.sleep(0.5)
            if connected:
                ctx3 = browser.contexts[0]
                pg = await ctx3.new_page()
                await pg.goto(MOCK, wait_until="domcontentloaded")
                await pg.fill("#prompt-input", "via CDP")
                await pg.click("#send")
                await wait_idle(pg)
                n = await pg.locator(".message").count()
                check("E-07 connect_over_cdp to externally launched Edge", n >= 4, f"messages={n}")
                await browser.close()
            else:
                check("E-07 connect_over_cdp to externally launched Edge", False, "could not connect within 20s")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()

    passed = sum(1 for c in EVIDENCE["checks"] if c["ok"])
    EVIDENCE["summary"] = {"passed": passed, "total": len(EVIDENCE["checks"])}
    (ARTIFACTS / "playwright-generic-evidence.json").write_text(json.dumps(EVIDENCE, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{passed}/{len(EVIDENCE['checks'])} checks passed")
    return 0 if passed == len(EVIDENCE["checks"]) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
