#!/usr/bin/env python3
"""Playwright probe: drive Microsoft Edge with the unpacked ContextFlow probe
extension and verify the browser-side mechanics end to end.

Covers (CF-EXP-001):
  B-01  extension loads in Edge (MV3, service worker present)
  B-02  content script reports page identity / session ref
  B-03  SPA pushState navigation is detected by webNavigation.onHistoryStateUpdated
  B-04  isolated-world limitation: content-script pushState patch sees nothing,
        MAIN-world bridge can read the page variable
  B-05  streaming state transitions (idle -> generating -> idle) observed via DOM
  B-06  background -> content messaging (DOM notice injected)
  B-07  Core -> extension -> content full loop over WebSocket
  B-08  popup page renders live state
  B-09  service worker termination: in-memory state lost, storage.session kept
  A-05  public chatgpt.com navigation in a clean profile (no credentials)

Usage:  python playwright_probe.py [--headless] [--keep-open]
Writes _artifacts/playwright-evidence.json and screenshots.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import traceback
from pathlib import Path

import requests
from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
EXT_DIR = HERE.parent / "edge-extension-probe"
ARTIFACTS = HERE.parent.parent / "_artifacts"
SHOTS = ARTIFACTS / "screenshots"
PROFILE = ARTIFACTS / "edge-profile-playwright"
MOCK = "http://127.0.0.1:8787/mock/chat/session-a"
CORE = "http://127.0.0.1:8765"
TOKEN = "dev-token-cf-exp-001"

EVIDENCE: dict = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "checks": [], "notes": []}


def check(name: str, ok: bool, detail="") -> bool:
    EVIDENCE["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail else ""), flush=True)
    return ok


def note(msg: str) -> None:
    EVIDENCE["notes"].append(msg)
    print("NOTE " + msg, flush=True)


async def poll(fn, timeout=10.0, interval=0.25):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = await fn()
        if last:
            return last
        await asyncio.sleep(interval)
    return last


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true", default=True)
    ap.add_argument("--headed", dest="headless", action="store_false")
    ap.add_argument("--keep-open", action="store_true")
    args = ap.parse_args()

    SHOTS.mkdir(parents=True, exist_ok=True)
    if PROFILE.exists():
        import shutil
        shutil.rmtree(PROFILE, ignore_errors=True)
    PROFILE.mkdir(parents=True, exist_ok=True)

    ext_args = [
        f"--disable-extensions-except={EXT_DIR}",
        f"--load-extension={EXT_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=msEdgeFirstRunExperience,msEdgeSidebarV2",
        "--disable-background-networking",
    ]

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(PROFILE),
            channel="msedge",
            headless=args.headless,
            args=ext_args,
            viewport={"width": 1280, "height": 860},
        )
        EVIDENCE["browser"] = {"channel": "msedge", "headless": args.headless, "version": ctx.browser.version if ctx.browser else "n/a"}

        sw = None
        try:
            if ctx.service_workers:
                sw = ctx.service_workers[0]
            else:
                sw = await ctx.wait_for_event("serviceworker", timeout=15000)
        except Exception as exc:  # noqa: BLE001
            check("B-01 extension service worker started", False, repr(exc))
            EVIDENCE["error"] = traceback.format_exc()
            await ctx.close()
            return 1

        ext_id = sw.url.split("/")[2]
        EVIDENCE["extension_id"] = ext_id
        check("B-01 extension service worker started", True, f"id={ext_id} url={sw.url}")

        manifest = await sw.evaluate("() => __cf.manifest()")
        EVIDENCE["manifest"] = manifest
        check("B-01b MV3 manifest visible in worker", manifest.get("manifest_version") == 3, json.dumps(manifest)[:300])

        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(MOCK, wait_until="domcontentloaded")
        await page.wait_for_selector("#conversation .message")
        await page.screenshot(path=str(SHOTS / "01-mock-session-a.png"))

        # --- B-02 page identity -------------------------------------------------
        async def get_state():
            try:
                return await sw.evaluate("() => __cf.getState()")
            except Exception:
                return None

        st = await poll(lambda: _tab_state(sw, page))
        if not st:
            check("B-02 content script reported page state", False, "no report within timeout")
        else:
            # The MAIN-world probe is asynchronous (scripting.executeScript round trip).
            st = await poll(lambda: _tab_state(sw, page, want_main_world=True), timeout=8) or st
            EVIDENCE["page_state_initial"] = st
            check("B-02 content script reported page state", st.get("kind") == "mock-chat", json.dumps(st)[:400])
            check("B-02b session ref from page DOM", (st.get("session") or {}).get("value") == "session-a", json.dumps(st.get("session")))
            check("B-02c message counts by role", (st.get("messages") or {}).get("total") == 2, json.dumps(st.get("messages")))
            iso = st.get("isolation") or {}
            check("B-04a isolated world cannot see page JS var", iso.get("isolatedWorldSeesPageVar") is False, f"isolatedWorldSeesPageVar={iso.get('isolatedWorldSeesPageVar')}")
            mw = iso.get("mainWorldProbe") or {}
            check("B-04b MAIN-world probe (chrome.scripting) reads page JS var", mw.get("available") is True and (mw.get("data") or {}).get("sessionId") == "session-a", json.dumps(mw)[:300])
            inj = iso.get("inlineInjection") or {}
            check("B-04d inline <script> injection blocked by CSP", inj.get("ran") is False, json.dumps(inj))

        # --- B-03 SPA navigation ------------------------------------------------
        await page.click('a.session-link[data-session-id="session-b"]')
        await page.wait_for_function("() => document.body.dataset.sessionId === 'session-b'")
        await asyncio.sleep(0.6)
        st2 = await poll(lambda: _tab_state(sw, page, want_session="session-b"))
        nav = await sw.evaluate("() => __cf.getState().navEvents")
        EVIDENCE["nav_events"] = nav
        hist = [e for e in nav if e.get("kind") == "webNavigation.onHistoryStateUpdated"]
        check("B-03a pushState navigation detected by webNavigation", bool(hist), f"{len(hist)} onHistoryStateUpdated event(s)")
        check("B-03b content script followed SPA navigation", bool(st2) and (st2.get("session") or {}).get("value") == "session-b", json.dumps(st2.get("session") if st2 else None))
        iso2 = (st2 or {}).get("isolation") or {}
        check("B-04c isolated-world pushState patch saw 0 page calls", iso2.get("isolatedPushStateCalls") == 0, f"count={iso2.get('isolatedPushStateCalls')}")
        check("B-03c onCommitted not fired for pushState", not [e for e in nav if e.get("kind") == "webNavigation.onCommitted" and e.get("url", "").endswith("session-b")], "no onCommitted for session-b")
        await page.screenshot(path=str(SHOTS / "02-mock-session-b.png"))

        # --- B-05 streaming -----------------------------------------------------
        await page.fill("#prompt-input", "Please produce a structured answer.")
        await page.click("#send")
        await page.wait_for_function("() => document.body.dataset.appState === 'generating'", timeout=5000)
        gen = await poll(lambda: _tab_state(sw, page, want_state="generating"), timeout=5)
        check("B-05a generating state observed", bool(gen) and gen.get("generating") is True, json.dumps(gen.get("appState") if gen else None))
        await page.wait_for_function("() => document.body.dataset.appState === 'idle'", timeout=15000)
        await asyncio.sleep(0.6)
        done = await poll(lambda: _tab_state(sw, page, want_last_status="complete"), timeout=5)
        EVIDENCE["page_state_after_reply"] = done
        check("B-05b reply completion observed", bool(done) and (done.get("messages") or {}).get("lastStatus") == "complete", json.dumps(done.get("messages") if done else None))
        check("B-05c message count grew by 2", bool(done) and (done.get("messages") or {}).get("total") == 4, json.dumps(done.get("messages") if done else None))
        await page.screenshot(path=str(SHOTS / "03-mock-after-reply.png"))

        # --- B-06 background -> content ----------------------------------------
        tab_id = await sw.evaluate("""async () => {
            const tabs = await chrome.tabs.query({});
            const t = tabs.find(t => (t.url||'').includes('127.0.0.1:8787'));
            return t ? t.id : null;
        }""")
        EVIDENCE["tab_id"] = tab_id
        await sw.evaluate("""async (tabId) => {
            await chrome.tabs.sendMessage(tabId, {type:'cf:core_notice', payload:{type:'manual_ping', task_id:'b06'}});
        }""", tab_id)
        await page.wait_for_selector("#cf-core-notice", timeout=5000)
        banner = await page.text_content("#cf-core-notice")
        check("B-06 background -> content message delivered", "manual_ping" in (banner or ""), (banner or "")[:160])

        # --- B-07 Core -> extension -> content over WebSocket -------------------
        ws_state = await poll(lambda: _ws_connected(sw), timeout=15)
        check("B-07a extension connected to local Core over WebSocket", bool(ws_state), json.dumps(ws_state)[:300])
        health = requests.get(f"{CORE}/health", timeout=5).json()
        EVIDENCE["core_health_with_extension"] = health
        check("B-07b Core sees the extension client", len(health.get("clients") or []) >= 1, json.dumps(health)[:300])
        r = requests.post(f"{CORE}/api/notify", json={"task_id": "test-task-001", "summary": "Mock worker finished.", "session_ref": "session-b"},
                          headers={"X-CF-Token": TOKEN}, timeout=5).json()
        EVIDENCE["notify_response"] = r
        check("B-07c Core push accepted", r.get("delivered") == 1, json.dumps(r))
        await page.wait_for_function("""() => {
            const el = document.getElementById('cf-core-notice');
            return el && el.textContent.includes('worker_completed');
        }""", timeout=8000)
        banner2 = await page.text_content("#cf-core-notice")
        check("B-07d Core -> SW -> content -> DOM loop complete", "test-task-001" in (banner2 or ""), (banner2 or "")[:200])
        await page.screenshot(path=str(SHOTS / "04-core-notice.png"))

        # --- B-08 popup ---------------------------------------------------------
        popup = await ctx.new_page()
        await popup.goto(f"chrome-extension://{ext_id}/popup.html", wait_until="domcontentloaded")
        await asyncio.sleep(1.5)
        popup_ws = await popup.text_content("#ws")
        popup_kind = await popup.text_content("#kind")
        popup_iso = await popup.text_content("#isolation")
        EVIDENCE["popup"] = {"ws": popup_ws, "kind": popup_kind, "isolation": popup_iso}
        check("B-08 popup renders live extension state", (popup_ws or "").strip() in ("connected", "connecting", "disconnected"), f"ws={popup_ws} kind={popup_kind}")
        await popup.screenshot(path=str(SHOTS / "05-popup.png"))
        await popup.close()

        # --- B-09 service worker termination -----------------------------------
        try:
            before = await sw.evaluate("() => __cf.getState().navEvents.length")
            inst_before = await sw.evaluate("() => __cf.getState().instanceId")
            await sw.evaluate("""async () => {
                await chrome.storage.session.set({ cfSentinel: 'before-restart', cfSentinelNavCount: __cf.getState().navEvents.length });
            }""")
            browser_cdp = await ctx.browser.new_browser_cdp_session()
            targets = await browser_cdp.send("Target.getTargets")
            sw_target = next((t for t in targets["targetInfos"] if t["type"] == "service_worker" and ext_id in t["url"]), None)
            if not sw_target:
                check("B-09 SW termination test", False, "service worker target not found")
            else:
                await browser_cdp.send("Target.closeTarget", {"targetId": sw_target["targetId"]})
                await asyncio.sleep(1.5)
                # Wake the worker with an extension page (runtime message), not a nav event.
                wake = await ctx.new_page()
                await wake.goto(f"chrome-extension://{ext_id}/popup.html", wait_until="domcontentloaded")
                await asyncio.sleep(2.0)
                sw2 = None
                for _ in range(20):
                    cands = [w for w in ctx.service_workers if w.url == sw.url]
                    if cands:
                        sw2 = cands[0]
                        break
                    await asyncio.sleep(0.25)
                if sw2 is None:
                    try:
                        sw2 = await ctx.wait_for_event("serviceworker", timeout=8000)
                    except Exception:
                        sw2 = None
                if sw2:
                    after_nav = await sw2.evaluate("() => __cf.getState().navEvents.length")
                    inst_after = await sw2.evaluate("() => __cf.getState().instanceId")
                    stored = await sw2.evaluate("async () => (await chrome.storage.session.get('cfSentinel')).cfSentinel || null")
                    EVIDENCE["sw_restart"] = {"before": before, "instance_before": inst_before, "instance_after": inst_after,
                                              "after_nav_in_memory": after_nav, "sentinel_in_storage": stored}
                    check("B-09 SW terminated and restarted (new instance)", inst_after != inst_before, f"{inst_before} -> {inst_after}")
                    check("B-09b in-memory navEvents lost on SW restart", after_nav < before, f"before={before} after={after_nav}")
                    check("B-09c storage.session survives SW restart", stored == "before-restart", f"sentinel={stored}")
                else:
                    check("B-09 SW restarted", False, "worker did not come back")
                await wake.close()
        except Exception as exc:  # noqa: BLE001
            EVIDENCE["sw_restart_error"] = traceback.format_exc()
            check("B-09 SW termination test", False, repr(exc))

        # --- A-05 public chatgpt.com in a clean profile -------------------------
        try:
            cp = await ctx.new_page()
            resp = await cp.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)
            info = {"status": resp.status if resp else None, "url": cp.url, "title": await cp.title()}
            EVIDENCE["chatgpt_public"] = info
            await cp.screenshot(path=str(SHOTS / "06-chatgpt-public.png"))
            note(f"chatgpt.com public navigation: {json.dumps(info)}")
            check("A-05 public chatgpt.com reachable in clean profile", resp is not None and resp.status < 500, json.dumps(info))
            await cp.close()
        except Exception as exc:  # noqa: BLE001
            EVIDENCE["chatgpt_public_error"] = repr(exc)
            note(f"chatgpt.com navigation failed: {exc!r}")

        EVIDENCE["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        passed = sum(1 for c in EVIDENCE["checks"] if c["ok"])
        EVIDENCE["summary"] = {"passed": passed, "total": len(EVIDENCE["checks"])}
        (ARTIFACTS / "playwright-evidence.json").write_text(json.dumps(EVIDENCE, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n{passed}/{len(EVIDENCE['checks'])} checks passed", flush=True)

        if not args.keep_open:
            await ctx.close()
        return 0 if passed == len(EVIDENCE["checks"]) else 1


async def _tab_state(sw, page, want_session=None, want_state=None, want_last_status=None, want_main_world=False):
    try:
        st = await sw.evaluate("() => __cf.getState()")
    except Exception:
        return None
    tabs = st.get("tabs") or {}
    for t in tabs.values():
        if "127.0.0.1:8787" not in (t.get("url") or ""):
            continue
        if want_session and (t.get("session") or {}).get("value") != want_session:
            continue
        if want_state and t.get("appState") != want_state:
            continue
        if want_last_status and (t.get("messages") or {}).get("lastStatus") != want_last_status:
            continue
        if want_main_world and not ((t.get("isolation") or {}).get("mainWorldProbe") or {}).get("available"):
            continue
        return t
    return None


async def _ws_connected(sw):
    try:
        st = await sw.evaluate("() => __cf.getState()")
    except Exception:
        return None
    return st if st.get("wsStatus") == "connected" else None


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
