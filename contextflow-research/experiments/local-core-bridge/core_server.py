#!/usr/bin/env python3
"""ContextFlow Core bridge - minimal research server (CF-EXP-001, task D).

Purpose: prove a *local* Python service can exchange events with a browser
extension in both directions, and measure what happens on the failure paths.

Design decisions (deliberately minimal, not production):
  * binds 127.0.0.1 only
  * one shared bearer token (dev only) - real design should use a per-install
    secret handed to the extension at pairing time
  * WebSocket for push (Core -> extension) + HTTP POST for event ingestion
  * Origin allow-list: chrome-extension://<id> or no Origin (native clients).
    Any http(s) Origin is rejected, so a random web page cannot drive the Core.
  * pending queue so notifications produced while the extension is offline are
    delivered on the next connect (tests "Core restart / extension offline").

Run:  python core_server.py --port 8765
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT.parent.parent / "_artifacts"
ARTIFACTS.mkdir(parents=True, exist_ok=True)
LOG_PATH = ARTIFACTS / "core-server.log"
EVENTS_PATH = ARTIFACTS / "core-events.jsonl"

DEV_TOKEN = os.environ.get("CF_CORE_TOKEN", "dev-token-cf-exp-001")
ALLOW_ANY_EXTENSION = True  # research mode: accept any chrome-extension:// origin

app = FastAPI(title="ContextFlow Core Bridge (research)", version="0.1.0")

state: dict[str, Any] = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "events": deque(maxlen=500),
    "clients": {},          # client_id -> WebSocket
    "pending": deque(),     # notifications queued while no client connected
    "counters": {"events_received": 0, "notifications_sent": 0, "rejected": 0},
}


def log(msg: str, **fields: Any) -> None:
    line = json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "msg": msg, **fields}, ensure_ascii=False)
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def origin_allowed(origin: str | None) -> bool:
    if origin is None or origin == "":
        return True  # native test clients / curl
    if origin.startswith("chrome-extension://") or origin.startswith("moz-extension://"):
        return True
    return False


def check_token(token: str | None) -> None:
    if token != DEV_TOKEN:
        state["counters"]["rejected"] += 1
        raise HTTPException(status_code=401, detail="invalid token")


def record_event(evt: dict[str, Any], source: str) -> dict[str, Any]:
    entry = {"received_at": datetime.now(timezone.utc).isoformat(), "source": source, **evt}
    state["events"].append(entry)
    state["counters"]["events_received"] += 1
    with EVENTS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "started_at": state["started_at"],
        "clients": list(state["clients"].keys()),
        "pending": len(state["pending"]),
        "counters": state["counters"],
    }


@app.get("/api/events")
async def get_events(limit: int = 50) -> dict[str, Any]:
    return {"events": list(state["events"])[-limit:]}


@app.post("/events")
async def post_event(request: Request, x_cf_token: str | None = Header(default=None)) -> JSONResponse:
    """HTTP fallback for extension -> Core events."""
    origin = request.headers.get("origin")
    if not origin_allowed(origin):
        state["counters"]["rejected"] += 1
        log("rejected_origin", origin=origin)
        return JSONResponse({"ok": False, "error": "origin not allowed"}, status_code=403)
    check_token(x_cf_token)
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"invalid json: {exc}"}, status_code=400)
    if not isinstance(body, dict) or "type" not in body:
        return JSONResponse({"ok": False, "error": "message must be an object with a type"}, status_code=400)
    entry = record_event(body, source="http")
    log("event_http", type=body.get("type"), origin=origin)
    return JSONResponse({"ok": True, "received": entry})


@app.post("/api/notify")
async def notify(payload: dict[str, Any], x_cf_token: str | None = Header(default=None)) -> dict[str, Any]:
    """Test harness / Worker completion -> queue or push to the extension."""
    check_token(x_cf_token)
    msg = {"type": "worker_completed", "at": datetime.now(timezone.utc).isoformat(), **payload}
    delivered = await broadcast(msg)
    if not delivered:
        state["pending"].append(msg)
        log("notification_queued", pending=len(state["pending"]))
    return {"ok": True, "delivered": delivered, "queued": not delivered, "pending": len(state["pending"])}


async def broadcast(msg: dict[str, Any]) -> int:
    dead: list[str] = []
    sent = 0
    for cid, ws in list(state["clients"].items()):
        try:
            await ws.send_text(json.dumps(msg, ensure_ascii=False))
            sent += 1
        except Exception:  # noqa: BLE001
            dead.append(cid)
    for cid in dead:
        state["clients"].pop(cid, None)
    if sent:
        state["counters"]["notifications_sent"] += sent
        log("notification_sent", clients=sent, type=msg.get("type"))
    return sent


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    origin = ws.headers.get("origin")
    token = ws.query_params.get("token")
    if not origin_allowed(origin):
        state["counters"]["rejected"] += 1
        log("ws_rejected_origin", origin=origin)
        await ws.close(code=4403)
        return
    if token != DEV_TOKEN:
        state["counters"]["rejected"] += 1
        log("ws_rejected_token")
        await ws.close(code=4401)
        return
    await ws.accept()
    client_id = f"client-{int(time.time() * 1000)}"
    state["clients"][client_id] = ws
    log("ws_connected", client_id=client_id, origin=origin, clients=len(state["clients"]))
    await ws.send_text(json.dumps({"type": "welcome", "client_id": client_id, "pending": len(state["pending"])}))
    # Flush notifications queued while the extension was offline.
    while state["pending"]:
        msg = state["pending"].popleft()
        await ws.send_text(json.dumps({**msg, "replayed": True}, ensure_ascii=False))
        log("notification_replayed", type=msg.get("type"))
    try:
        while True:
            raw = await ws.receive_text()
            try:
                evt = json.loads(raw)
            except json.JSONDecodeError:
                log("invalid_json", raw=raw[:200])
                continue
            if not isinstance(evt, dict) or "type" not in evt:
                log("invalid_message", raw=raw[:200])
                continue
            record_event(evt, source="ws")
            log("event_ws", type=evt.get("type"), client_id=client_id)
            if evt.get("type") == "ping":
                await ws.send_text(json.dumps({"type": "pong", "at": datetime.now(timezone.utc).isoformat()}))
    except WebSocketDisconnect:
        pass
    finally:
        state["clients"].pop(client_id, None)
        log("ws_disconnected", client_id=client_id, clients=len(state["clients"]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    log("core_starting", host=args.host, port=args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
