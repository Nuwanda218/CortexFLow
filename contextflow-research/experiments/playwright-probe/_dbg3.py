import asyncio, json, shutil, subprocess, sys, threading, time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import requests
from playwright.async_api import async_playwright

ROOT = Path(r"D:\CortexFlow\contextflow-research")
EXT = ROOT / "contextflow-extension"
MOCK = ROOT / "_artifacts" / "cfdev003-mock"
PROFILE = ROOT / "_artifacts" / "edge-profile-dbg3"
PORT = 8894

class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a): pass

async def main():
    shutil.rmtree(PROFILE, ignore_errors=True)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), partial(Quiet, directory=str(MOCK)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    bridge = subprocess.Popen([sys.executable, str(EXT / "bridge_server.py"), "--port", "8766"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                requests.get("http://127.0.0.1:8766/health", timeout=1); break
            except Exception: time.sleep(0.3)
        async with async_playwright() as p:
            ctx = await p.chromium.launch_persistent_context(str(PROFILE), channel="msedge", headless=True,
                args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}", "--no-first-run"])
            sw = ctx.service_workers[0] if ctx.service_workers else await ctx.wait_for_event("serviceworker", timeout=20000)
            page = await ctx.new_page()
            logs = []
            page.on("console", lambda m: logs.append(f"[{m.type}] {m.text[:160]}"))
            await page.goto(f"http://127.0.0.1:{PORT}/mock.html?s=aaaa1111-0000-4000-8000-00000000000a", wait_until="domcontentloaded")
            await asyncio.sleep(2)
            await page.evaluate("() => window.__mock.seedCompleted()")
            await asyncio.sleep(1)
            # read snapshot via SW message directly
            tab_id = await sw.evaluate("""async () => { const tabs = await chrome.tabs.query({}); const t = tabs.find(t => (t.url||'').includes('mock.html')); return t ? t.id : null; }""")
            snap = await sw.evaluate("""async (tabId) => await new Promise((res) => chrome.tabs.sendMessage(tabId, {type:"cf:read_snapshot", request_id:"dbg", expected_session_id:null}, (r) => res(r)))""", tab_id)
            print("SNAPSHOT:", json.dumps(snap, ensure_ascii=False)[:1200])
            print("--- console ---")
            for l in logs[-10:]: print(l)
            await ctx.close()
    finally:
        server.shutdown(); bridge.terminate()

asyncio.run(main())
