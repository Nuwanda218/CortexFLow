#!/usr/bin/env python3
"""CF-DEV-002 external test client (stage D).

Usage:
  python cfdev002_client.py list
  python cfdev002_client.py snapshot <session_id> [tab_id]
"""
from __future__ import annotations

import asyncio
import json
import sys

import websockets  # from playwright's bundled deps? no: pip install websockets

URI = "ws://127.0.0.1:8766/ws?token=dev-token-cf-exp-002"


async def call(payload: dict) -> dict:
    async with websockets.connect(URI, origin="http://127.0.0.1:8766") as ws:
        await ws.send(json.dumps(payload))
        while True:
            resp = json.loads(await ws.recv())
            if resp.get("type") == "welcome":
                continue
            if resp.get("id") == payload.get("id"):
                return resp


def summarize(resp: dict) -> str:
    if not resp.get("success"):
        err = resp.get("error", {})
        return f"FAIL {resp.get('id')} {err.get('code')}: {err.get('message')}"
    data = resp.get("data", {})
    if "sessions" in data:
        lines = [f"OK {resp['id']} {len(data['sessions'])} tab(s):"]
        for s in data["sessions"]:
            lines.append(f"  tab={s['tab_id']} session={s.get('session_id')} kind={s.get('kind')} ready={s.get('ready')}")
        return "\n".join(lines)
    snap = data.get("snapshot", {})
    msgs = snap.get("messages", [])
    lines = [
        f"OK {resp['id']} tab={data.get('tab_id')} captured_at={data.get('captured_at')}",
        f"  session={snap.get('session', {}).get('session_id')} kind={snap.get('session', {}).get('kind')}",
        f"  generation={snap.get('generation', {}).get('state')} messages={len(msgs)}",
    ]
    for m in msgs[-3:]:
        preview = (m.get("content") or "")[:50].replace("\n", " ")
        lines.append(f"  [{m.get('order')}] {m.get('role')}: {preview}… ({m.get('text_length')} chars)")
    return "\n".join(lines)


async def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        resp = await call({"id": "req-list-001", "type": "list_sessions"})
    elif cmd == "snapshot":
        session_id = sys.argv[2]
        payload = {"id": "req-snap-001", "type": "get_snapshot", "session_id": session_id}
        if len(sys.argv) > 3:
            payload["tab_id"] = int(sys.argv[3])
        resp = await call(payload)
    else:
        print(__doc__)
        return 2
    print(summarize(resp))
    return 0 if resp.get("success") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
