#!/usr/bin/env python3
"""Iframe embedding probe (CF-EXP-001, task F1).

Question: can a normal web page iframe chatgpt.com?
Method: serve a local page that embeds https://chatgpt.com/ in an iframe and
observe (a) whether the frame commits, (b) console/CSP errors, (c) the response
headers of the embedded document (X-Frame-Options / CSP frame-ancestors).

No credentials are used; the profile is fresh.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
ARTIFACTS = HERE.parent.parent / "_artifacts"
PROFILE = ARTIFACTS / "edge-profile-iframe"
HOST_PAGE = ARTIFACTS / "iframe-host.html"


async def main() -> int:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(PROFILE, ignore_errors=True)
    HOST_PAGE.write_text(
        """<!doctype html><html><head><meta charset="utf-8"><title>iframe host</title></head>
<body style="margin:0;background:#111;color:#eee;font:14px sans-serif">
<h3>Local host page embedding chatgpt.com in an iframe</h3>
<iframe id="target" src="https://chatgpt.com/" style="width:1100px;height:700px;border:1px solid #444"></iframe>
</body></html>""",
        encoding="utf-8",
    )

    evidence: dict = {"checks": [], "console": [], "frame_requests": []}
    def check(name, ok, detail=""):
        evidence["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail else ""), flush=True)

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(str(PROFILE), channel="msedge", headless=True,
                                                         args=["--no-first-run", "--no-default-browser-check"])
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        page.on("console", lambda m: evidence["console"].append({"type": m.type, "text": m.text[:400]}))

        async def on_response(resp):
            if "chatgpt.com" in resp.url:
                try:
                    hdrs = await resp.all_headers()
                except Exception:
                    hdrs = {}
                evidence["frame_requests"].append({
                    "url": resp.url, "status": resp.status,
                    "x_frame_options": hdrs.get("x-frame-options"),
                    "csp": hdrs.get("content-security-policy"),
                })
        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        await page.goto(HOST_PAGE.as_uri(), wait_until="domcontentloaded")
        await asyncio.sleep(8)

        frames = page.frames
        target = next((f for f in frames if "chatgpt.com" in (f.url or "")), None)
        evidence["frames"] = [f.url for f in frames]
        evidence["target_frame_url"] = target.url if target else None
        blocked = target is None or (target.url or "").startswith("chrome-error://")
        check("F1-01 iframe element exists", await page.locator("#target").count() == 1)
        check("F1-02 iframe embedding blocked (error page, no chatgpt.com document)", blocked, f"frames={evidence['frames']}")
        if target:
            try:
                title = await target.title()
            except Exception as exc:  # noqa: BLE001
                title = f"<error: {exc}>"
            evidence["target_frame_title"] = title
        hdrs = [r for r in evidence["frame_requests"] if r["status"]]
        xfo = [r["x_frame_options"] for r in hdrs if r["x_frame_options"]]
        csp = [r["csp"] for r in hdrs if r["csp"]]
        evidence["x_frame_options_seen"] = xfo
        evidence["csp_seen"] = csp
        check("F1-04 chatgpt.com sends X-Frame-Options: SAMEORIGIN", "SAMEORIGIN" in xfo, f"{xfo[:2]}")
        check("F1-05 blocking mechanism is X-Frame-Options (no CSP frame-ancestors seen)",
              not any("frame-ancestors" in (c or "") for c in csp), f"csp_count={len(csp)}")

        await page.screenshot(path=str(ARTIFACTS / "screenshots" / "07-iframe-host.png"))
        await ctx.close()

    passed = sum(1 for c in evidence["checks"] if c["ok"])
    evidence["summary"] = {"passed": passed, "total": len(evidence["checks"])}
    (ARTIFACTS / "iframe-evidence.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{passed}/{len(evidence['checks'])} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
