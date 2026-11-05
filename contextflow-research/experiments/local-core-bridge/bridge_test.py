#!/usr/bin/env python3
"""Headless protocol test for the Core bridge (CF-D-01..D-03).

Runs without a browser: acts as a fake extension client over WebSocket and as a
fake web page over HTTP, so the transport contract can be verified even if the
browser part of the experiment is blocked.

Usage:  python bridge_test.py [--port 8765]
Exit code 0 = all assertions passed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import requests
import websockets

TOKEN = "dev-token-cf-exp-001"
RESULTS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "ok": ok, "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail else ""), flush=True)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    ws_url = f"ws://127.0.0.1:{args.port}/ws?token={TOKEN}"

    # --- D-01: extension -> Core over WebSocket -----------------------------
    async with websockets.connect(ws_url, origin="chrome-extension://abcdefghijklmnop") as ws:
        welcome = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        check("D-01a welcome on connect", welcome.get("type") == "welcome", str(welcome))
        await ws.send(json.dumps({
            "type": "session_changed",
            "source": "edge_extension",
            "session_ref": "local-test-session-a",
        }))
        await asyncio.sleep(0.3)
        ev = requests.get(f"{base}/api/events", timeout=5).json()["events"]
        got = [e for e in ev if e.get("type") == "session_changed"]
        check("D-01b event ingested from WS", bool(got), f"{len(got)} event(s)")

        # --- D-02: Core -> extension push -----------------------------------
        r = requests.post(f"{base}/api/notify", json={"task_id": "test-task-001", "summary": "Mock worker finished."},
                          headers={"X-CF-Token": TOKEN}, timeout=5).json()
        check("D-02a notify delivered to connected client", r.get("delivered") == 1, str(r))
        pushed = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        check("D-02b push payload shape", pushed.get("type") == "worker_completed" and pushed.get("task_id") == "test-task-001", str(pushed))

        # --- D-03a: invalid message -----------------------------------------
        await ws.send("this is not json")
        await ws.send(json.dumps({"no_type": True}))
        await asyncio.sleep(0.2)
        h = requests.get(f"{base}/health", timeout=5).json()
        check("D-03a invalid messages do not kill the connection", h.get("clients") and len(h["clients"]) == 1, str(h))

        # --- D-03b: duplicate message ---------------------------------------
        dup = {"type": "session_changed", "source": "edge_extension", "session_ref": "dup-test"}
        await ws.send(json.dumps(dup))
        await ws.send(json.dumps(dup))
        await asyncio.sleep(0.3)
        ev = requests.get(f"{base}/api/events", timeout=5).json()["events"]
        n_dup = len([e for e in ev if e.get("session_ref") == "dup-test"])
        check("D-03b duplicate messages are both accepted (no dedupe in v1)", n_dup == 2, f"count={n_dup}")

    # --- D-03c: notification while no client connected -> queued ------------
    await asyncio.sleep(0.4)
    r = requests.post(f"{base}/api/notify", json={"task_id": "offline-task", "summary": "queued while offline"},
                      headers={"X-CF-Token": TOKEN}, timeout=5).json()
    check("D-03c notification queued when extension offline", r.get("queued") is True and r.get("delivered") == 0, str(r))

    # --- D-03d: reconnect replays pending ----------------------------------
    async with websockets.connect(ws_url, origin="chrome-extension://abcdefghijklmnop") as ws:
        msgs = []
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                msgs.append(json.loads(await asyncio.wait_for(ws.recv(), timeout=1)))
            except asyncio.TimeoutError:
                break
        replayed = [m for m in msgs if m.get("replayed")]
        check("D-03d pending notification replayed on reconnect", any(m.get("task_id") == "offline-task" for m in replayed), str(msgs))

    # --- D-03e: auth failures ----------------------------------------------
    try:
        async with websockets.connect(f"ws://127.0.0.1:{args.port}/ws?token=wrong", origin="chrome-extension://abc") as ws:
            await ws.recv()
        check("D-03e wrong token rejected", False, "connected unexpectedly")
    except Exception as exc:  # noqa: BLE001
        check("D-03e wrong token rejected", True, type(exc).__name__)

    # --- D-03f: web-page Origin rejected -----------------------------------
    r = requests.post(f"{base}/events", json={"type": "session_changed"}, headers={"Origin": "https://evil.example"}, timeout=5)
    check("D-03f http(s) Origin rejected", r.status_code == 403, f"status={r.status_code}")

    # --- D-03g: extension Origin accepted over HTTP ------------------------
    r = requests.post(f"{base}/events", json={"type": "session_changed", "source": "http_fallback"},
                      headers={"Origin": "chrome-extension://abcdefghijklmnop", "X-CF-Token": TOKEN}, timeout=5)
    check("D-03g chrome-extension Origin accepted over HTTP", r.status_code == 200, f"status={r.status_code}")

    # --- D-03h: missing token rejected -------------------------------------
    r = requests.post(f"{base}/events", json={"type": "x"}, headers={"Origin": "chrome-extension://abc"}, timeout=5)
    check("D-03h missing token rejected", r.status_code == 401, f"status={r.status_code}")

    # --- D-03i: Core restart loses in-memory events (documented limitation) -
    check("D-03i (informational) events are in-memory only; see report", True, "restart clears /api/events")

    passed = sum(1 for r in RESULTS if r["ok"])
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    out = Path(__file__).resolve().parent.parent.parent / "_artifacts" / "bridge-test-results.json"
    out.write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
