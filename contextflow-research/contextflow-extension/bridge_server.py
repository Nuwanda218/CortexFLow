#!/usr/bin/env python3
"""ContextFlow WS bridge server (CF-DEV-002, stage C).

Reuses the CF-EXP-001 patterns (127.0.0.1 bind, token auth, Origin allow-list)
but implements a REQUEST-RESPONSE protocol instead of notifications:

  External client ──WS {id,type,...}──▶ bridge ──HTTP POST /internal/relay──▶ itself
                                                  (page pulls via WS push)

Flow for get_snapshot:
  1. client sends {id, type:"get_snapshot", session_id}
  2. bridge pushes {type:"bridge_request", request:{...}} to the connected
     browser page client (bridge_client.html served at /bridge_client.html)
  3. that page relays into the extension (bridge_relay.js -> background.js),
     which routes to the right content script and returns a result
  4. the page posts the result back to the bridge, the bridge matches id and
     replies to the original client

No chat content is logged. Token + Origin checks identical to CF-EXP-001.
Run: python bridge_server.py --port 8766
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

DEV_TOKEN = os.environ.get("CF_BRIDGE_TOKEN", "dev-token-cf-exp-002")
REQUEST_TIMEOUT_S = 12.0

app = FastAPI(title="ContextFlow WS Bridge (CF-DEV-002)", version="0.2.0")

state = {
    "clients": {},          # client_id -> WebSocket
    "page_client": None,    # the browser page client id (single for v1)
    "pending": {},          # request_id -> asyncio.Future
}

BRIDGE_CLIENT_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>ContextFlow bridge client</title></head>
<body>
<h3>ContextFlow bridge client (keep this tab open)</h3>
<div id="status">connecting…</div>
<script>
  const token = new URLSearchParams(location.search).get("token") || "";
  let ws = null;
  let attempt = 0;
  function connect() {
    ws = new WebSocket(`ws://127.0.0.1:8766/ws_page?token=${encodeURIComponent(token)}`);
    ws.onopen = () => { attempt = 0; document.getElementById("status").textContent = "connected to bridge"; };
    ws.onclose = () => {
      document.getElementById("status").textContent = "disconnected, retrying…";
      attempt += 1;
      setTimeout(connect, Math.min(10000, 1000 * attempt));
    };
    ws.onmessage = async (ev) => {
      let msg; try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.type !== "bridge_request") return;
      const requestId = msg.request && msg.request.id;
      // Relay into the extension via the bridge_relay content script.
      const result = await new Promise((resolve) => {
        const handler = (e) => {
          if (e.detail && e.detail.request_id === requestId) {
            window.removeEventListener("cf:bridge_result", handler);
            clearTimeout(guard);
            resolve(e.detail.result);
          }
        };
        const guard = setTimeout(() => {
          window.removeEventListener("cf:bridge_result", handler);
          resolve({ id: requestId, type: "result", success: false,
                    error: { code: "READ_TIMEOUT", message: "extension relay timeout" } });
        }, 10000);
        window.addEventListener("cf:bridge_result", handler);
        window.dispatchEvent(new CustomEvent("cf:bridge_request", { detail: { request_id: requestId, request: msg.request } }));
      });
      ws.send(JSON.stringify({ type: "bridge_result", request_id: requestId, result }));
    };
  }
  connect();
  window.__cfBridgePage = { isConnected: () => ws && ws.readyState === 1 };
</script>
</body></html>"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_token(token: str | None) -> None:
    if token != DEV_TOKEN:
        raise HTTPException(status_code=401, detail="invalid token")


def origin_allowed(origin: str | None) -> bool:
    if origin is None or origin == "":
        return True  # native test clients
    return origin.startswith("http://127.0.0.1") or origin.startswith("chrome-extension://")


@app.get("/bridge_client.html")
async def bridge_client_page() -> HTMLResponse:
    return HTMLResponse(BRIDGE_CLIENT_HTML)


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "page_connected": state["page_client"] is not None,
        "clients": list(state["clients"].keys()),
        "pending_requests": len(state["pending"]),
    }


@app.post("/internal/relay_result")
async def relay_result(payload: dict, x_cf_token: str | None = Header(default=None)) -> dict:
    check_token(x_cf_token)
    request_id = payload.get("request_id")
    fut = state["pending"].pop(request_id, None)
    if fut and not fut.done():
        fut.set_result(payload.get("result"))
        return {"ok": True}
    return {"ok": False, "reason": "unknown_or_finished_request"}


async def relay_to_page(request: dict) -> dict | None:
    """Push a request to the browser page client and await its result."""
    if state["page_client"] is None:
        return None
    request_id = request.get("id")
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    state["pending"][request_id] = fut
    ws = state["clients"].get(state["page_client"])
    try:
        await ws.send_text(json.dumps({"type": "bridge_request", "request": request}))
    except Exception:
        state["pending"].pop(request_id, None)
        return None
    try:
        return await asyncio.wait_for(fut, timeout=REQUEST_TIMEOUT_S)
    except asyncio.TimeoutError:
        state["pending"].pop(request_id, None)
        return {"id": request_id, "type": "result", "success": False,
                "error": {"code": "READ_TIMEOUT", "message": f"no result within {REQUEST_TIMEOUT_S}s"}}


async def ws_common(ws: WebSocket, is_page: bool) -> None:
    token = ws.query_params.get("token")
    if token != DEV_TOKEN:
        await ws.close(code=4401, reason="invalid token")
        return
    origin = ws.headers.get("origin")
    if not origin_allowed(origin):
        await ws.close(code=4403, reason="origin not allowed")
        return
    await ws.accept()
    client_id = f"client-{int(time.time() * 1000)}"
    state["clients"][client_id] = ws
    if is_page:
        state["page_client"] = client_id
    await ws.send_text(json.dumps({"type": "welcome", "client_id": client_id, "at": now()}))
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_text(json.dumps({"id": None, "type": "result", "success": False,
                                               "error": {"code": "INVALID_REQUEST", "message": "invalid JSON"}}))
                continue

            if is_page:
                if msg.get("type") == "bridge_result":
                    request_id = msg.get("request_id")
                    fut = state["pending"].pop(request_id, None)
                    if fut and not fut.done():
                        fut.set_result(msg.get("result"))
                continue  # page client does not issue requests over this socket

            # External client request
            request_id = msg.get("id")
            if not isinstance(request_id, str) or not request_id:
                await ws.send_text(json.dumps({"id": None, "type": "result", "success": False,
                                               "error": {"code": "INVALID_REQUEST", "message": "missing id"}}))
                continue
            mtype = msg.get("type")
            if mtype not in ("list_sessions", "get_snapshot", "get_generation"):
                await ws.send_text(json.dumps({"id": request_id, "type": "result", "success": False,
                                               "error": {"code": "INVALID_REQUEST", "message": f"unknown type {mtype}"}}))
                continue
            if mtype in ("get_snapshot", "get_generation") and not msg.get("session_id") and msg.get("tab_id") is None:
                await ws.send_text(json.dumps({"id": request_id, "type": "result", "success": False,
                                               "error": {"code": "INVALID_REQUEST", "message": "session_id or tab_id required"}}))
                continue
            if state["page_client"] is None:
                await ws.send_text(json.dumps({"id": request_id, "type": "result", "success": False,
                                               "error": {"code": "CONTENT_SCRIPT_UNAVAILABLE",
                                                         "message": "browser page client not connected; open /bridge_client.html"}}))
                continue
            result = await relay_to_page(msg)
            if result is None:
                result = {"id": request_id, "type": "result", "success": False,
                          "error": {"code": "CONTENT_SCRIPT_UNAVAILABLE", "message": "bridge page unreachable"}}
            await ws.send_text(json.dumps(result, ensure_ascii=False))
    except WebSocketDisconnect:
        pass
    finally:
        state["clients"].pop(client_id, None)
        if state["page_client"] == client_id:
            state["page_client"] = None


@app.websocket("/ws")
async def ws_external(ws: WebSocket) -> None:
    await ws_common(ws, is_page=False)


@app.websocket("/ws_page")
async def ws_page(ws: WebSocket) -> None:
    await ws_common(ws, is_page=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
