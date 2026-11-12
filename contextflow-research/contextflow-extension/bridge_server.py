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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

DEV_TOKEN = os.environ.get("CF_BRIDGE_TOKEN", "dev-token-cf-exp-002")
REQUEST_TIMEOUT_S = 12.0
SUBMISSION_TIMEOUT_S = 20.0
PENDING_SUBMISSION_TTL_S = 600.0
try:
    PENDING_CONTROL_TTL_S = float(os.environ.get("CF_BRIDGE_CONTROL_TTL_S", "120"))
except ValueError:
    PENDING_CONTROL_TTL_S = 120.0
if not (0 < PENDING_CONTROL_TTL_S <= 600):
    PENDING_CONTROL_TTL_S = 120.0
MAX_CONTROL_ID_LENGTH = 256
MAX_CONTROL_FRAME_BYTES = 64 * 1024
CONTROL_ACTIONS = frozenset(("bind_supervisor", "get_submission_status", "ping_core", "submit_report_review"))

app = FastAPI(title="ContextFlow WS Bridge (CF-ADAPTER-001)", version="0.3.0")

state = {
    "clients": {},          # client_id -> WebSocket
    "page_client": None,    # the browser page client id (single for v1)
    "external_client": None,
    "pending": {},          # request_id -> asyncio.Future
    "pending_submissions": {},  # submission_id -> {request_id, envelope, at, acked}
    "pending_controls": {},  # bridge_control_id -> page/core ownership + request identity
    "control_expiry_tasks": {},  # bridge_control_id -> asyncio.Task
}

# Read-only access to the CF-CORE-001 endpoint registry.  The bridge never
# creates or mutates endpoints; it only answers "which endpoint_id belongs to
# this native ChatGPT session?" for the Adapter and resolves an endpoint_id to
# its native session for reverse sends.
_REGISTRY_CACHE: dict = {"path": None, "mtime": None, "by_native": {}, "by_id": {}}


def _registry_path() -> Path:
    configured = os.environ.get("CONTEXTFLOW_ENDPOINT_REGISTRY")
    if configured:
        return Path(configured).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "ContextFlow" / "endpoints.json"
    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        return Path(state_home) / "contextflow" / "endpoints.json"
    return Path.home() / ".local" / "state" / "contextflow" / "endpoints.json"


def _load_registry_index() -> dict:
    """Load the endpoint registry (read-only) and cache it by file mtime."""

    path = _registry_path()
    try:
        mtime = path.stat().st_mtime if path.exists() else None
    except OSError:
        mtime = None
    if _REGISTRY_CACHE["path"] == str(path) and _REGISTRY_CACHE["mtime"] == mtime:
        return _REGISTRY_CACHE

    by_native: dict[tuple[str, str], str] = {}
    by_id: dict[str, dict] = {}
    if mtime is not None:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(document, dict) and document.get("schema_version") == 1:
                for record in document.get("endpoints") or []:
                    if not isinstance(record, dict):
                        continue
                    endpoint_id = record.get("endpoint_id")
                    provider = record.get("provider")
                    native_session_id = record.get("native_session_id")
                    if not (isinstance(endpoint_id, str) and isinstance(provider, str)
                            and isinstance(native_session_id, str)):
                        continue
                    by_native[(provider, native_session_id)] = endpoint_id
                    by_id[endpoint_id] = {
                        "endpoint_id": endpoint_id,
                        "provider": provider,
                        "native_session_id": native_session_id,
                        "session_kind": record.get("session_kind"),
                    }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            by_native, by_id = {}, {}
    _REGISTRY_CACHE.update({"path": str(path), "mtime": mtime,
                            "by_native": by_native, "by_id": by_id})
    return _REGISTRY_CACHE


def endpoint_for_session(native_session_id: str) -> str | None:
    index = _load_registry_index()
    return index["by_native"].get(("chatgpt_web", native_session_id))


def session_for_endpoint(endpoint_id: str) -> dict | None:
    index = _load_registry_index()
    return index["by_id"].get(endpoint_id)

BRIDGE_CLIENT_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>ContextFlow bridge client</title></head>
<body>
<h3>ContextFlow bridge client (keep this tab open)</h3>
<div id="status">connecting…</div>
<script>
  const token = new URLSearchParams(location.search).get("token") || "";
  let ws = null;
  let attempt = 0;
  const pendingPage = new Map(); // request_id -> {resolve, timer}

  function pageResult(ok, status, extra) {
    return Object.assign({ ok: ok, status: status }, extra || {});
  }

  function requestPage(payload, timeoutMs) {
    return new Promise((resolve) => {
      if (!ws || ws.readyState !== 1) {
        resolve(pageResult(false, "failed", { error: { code: "BRIDGE_DISCONNECTED", message: "bridge page websocket not connected" } }));
        return;
      }
      const requestId = payload.request_id || ("page-" + Math.random().toString(36).slice(2));
      const timer = setTimeout(() => {
        pendingPage.delete(requestId);
        resolve(pageResult(false, "unknown", { error: { code: "ACK_TIMEOUT", message: "no acknowledgement within " + timeoutMs + "ms" } }));
      }, timeoutMs);
      pendingPage.set(requestId, {
        resolve: (value) => { clearTimeout(timer); resolve(value); },
      });
      try {
        ws.send(JSON.stringify(Object.assign({}, payload, { request_id: requestId })));
      } catch (err) {
        clearTimeout(timer);
        pendingPage.delete(requestId);
        resolve(pageResult(false, "failed", { error: { code: "BRIDGE_DISCONNECTED", message: String(err) } }));
      }
    });
  }

  function connect() {
    ws = new WebSocket(`ws://${location.host}/ws_page?token=${encodeURIComponent(token)}`);
    ws.onopen = () => { attempt = 0; document.getElementById("status").textContent = "connected to bridge"; };
    ws.onclose = () => {
      document.getElementById("status").textContent = "disconnected, retrying…";
      attempt += 1;
      setTimeout(connect, Math.min(10000, 1000 * attempt));
    };
    ws.onmessage = async (ev) => {
      let msg; try { msg = JSON.parse(ev.data); } catch { return; }

      // Dev utility: after an extension reload the old relay content script is
      // dead; the bridge can ask the page to reload itself so the new relay is
      // injected without touching the browser UI.
      if (msg.type === "page_reload") { location.reload(); return; }

      if (msg.type === "submission_ack") {
        const pending = pendingPage.get(msg.request_id);
        if (pending) {
          pendingPage.delete(msg.request_id);
          pending.resolve(pageResult(msg.status === "accepted", msg.status, {
            submission_id: msg.submission_id,
            duplicate: msg.duplicate === true,
            error: msg.error || null,
          }));
        }
        return;
      }

      if (msg.type === "endpoint_lookup_result") {
        const pending = pendingPage.get(msg.request_id);
        if (pending) {
          pendingPage.delete(msg.request_id);
          pending.resolve(pageResult(true, "ok", {
            endpoint_id: msg.endpoint_id || null,
            found: msg.found === true,
            native_session_id: msg.native_session_id || null,
            error: msg.error || null,
          }));
        }
        return;
      }

      if (msg.type === "supervisor_control_result") {
        const pending = pendingPage.get(msg.request_id);
        if (pending) {
          pendingPage.delete(msg.request_id);
          if (msg.success === true) {
            pending.resolve(pageResult(true, "ok", { data: msg.data || {} }));
          } else {
            pending.resolve(pageResult(false, "failed", {
              error: msg.error || { code: "CORE_PROTOCOL_ERROR", message: "Core returned an invalid control result" },
            }));
          }
        }
        return;
      }

      if (msg.type !== "bridge_request") return;
      const requestId = msg.request && msg.request.id;
      // send_message waits for send verification inside the content script.
      const guardMs = (msg.request && (msg.request.type === "send_message" || msg.request.type === "send_message_to_endpoint")) ? 45000 : 10000;
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
        }, guardMs);
        window.addEventListener("cf:bridge_result", handler);
        window.dispatchEvent(new CustomEvent("cf:bridge_request", { detail: { request_id: requestId, request: msg.request } }));
      });
      // If the extension was reloaded, this page's relay content script is dead.
      // Reload ourselves so the new relay is injected (self-healing after reload_extension).
      const errMsg = (result && result.error && result.error.message) || "";
      const relayDead = (result && result.error) && (
        (/extension relay timeout/i.test(errMsg) && result.error.code === "READ_TIMEOUT")
        || /Extension context invalidated/i.test(errMsg)
      );
      if (relayDead && (!msg.request || msg.request.type !== "send_message")) {
        setTimeout(() => location.reload(), 800);
      }
      ws.send(JSON.stringify({ type: "bridge_result", request_id: requestId, result }));
    };
  }

  // Adapter -> external receiver (paper-plane submission).
  window.addEventListener("cf:submit_envelope", async (ev) => {
    const detail = ev.detail || {};
    const result = await requestPage({
      type: "adapter_submission",
      request_id: detail.request_id,
      envelope: detail.envelope,
    }, 18000);
    window.dispatchEvent(new CustomEvent("cf:submit_result", {
      detail: { request_id: detail.request_id, result: result },
    }));
  });

  // Adapter -> bridge: resolve the Endpoint ID of the current native session.
  window.addEventListener("cf:endpoint_lookup", async (ev) => {
    const detail = ev.detail || {};
    const result = await requestPage({
      type: "endpoint_lookup",
      request_id: detail.request_id,
      session_id: detail.session_id,
    }, 6000);
    window.dispatchEvent(new CustomEvent("cf:endpoint_lookup_result", {
      detail: { request_id: detail.request_id, result: result },
    }));
  });

  // Adapter -> Core: authenticated Supervisor bind/status control.
  window.addEventListener("cf:supervisor_control_request", async (ev) => {
    const detail = ev.detail || {};
    const result = await requestPage({
      type: "supervisor_control_request",
      request_id: detail.request_id,
      action: detail.action,
      params: detail.params,
    }, 18000);
    window.dispatchEvent(new CustomEvent("cf:supervisor_control_result", {
      detail: { request_id: detail.request_id, result: result },
    }));
  });

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
    if "?" in origin or "#" in origin:
        return False
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        return False
    if parsed.netloc.endswith(":"):
        return False
    if parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost"):
        return parsed.path in ("", "/")
    if parsed.scheme == "chrome-extension" and parsed.hostname and parsed.path in ("", "/"):
        return port is None
    return False


@app.get("/bridge_client.html")
async def bridge_client_page() -> HTMLResponse:
    return HTMLResponse(BRIDGE_CLIENT_HTML)


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "page_connected": state["page_client"] is not None,
        "external_clients": len(external_client_sockets()),
        "pending_requests": len(state["pending"]),
        "pending_submissions": len(state["pending_submissions"]),
        "pending_controls": len(state["pending_controls"]),
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


@app.post("/internal/reload_page")
async def reload_page(x_cf_token: str | None = Header(default=None)) -> dict:
    """Ask the connected bridge page to reload itself (dev recovery utility)."""

    check_token(x_cf_token)
    ok = await send_to_page({"type": "page_reload"})
    return {"ok": ok}


async def relay_to_page(request: dict) -> dict | None:
    """Push a request to the browser page client and await its result."""
    if state["page_client"] is None:
        return None
    request_id = request.get("id")
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    state["pending"][request_id] = fut
    ws = state["clients"].get(state["page_client"])
    # send_message waits for send verification (up to ~20s in the content script).
    timeout = 45.0 if request.get("type") in ("send_message", "send_message_to_endpoint") else REQUEST_TIMEOUT_S
    try:
        try:
            await ws.send_text(json.dumps({"type": "bridge_request", "request": request}))
        except Exception:
            return None
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        return {"id": request_id, "type": "result", "success": False,
                "error": {"code": "READ_TIMEOUT", "message": f"no result within {timeout}s"}}
    finally:
        state["pending"].pop(request_id, None)


def external_client_sockets() -> list[tuple[str, WebSocket]]:
    client_id = state["external_client"]
    ws = state["clients"].get(client_id)
    return [(client_id, ws)] if ws is not None else []


async def broadcast_to_external(payload: dict) -> int:
    """Best-effort broadcast to external clients; returns the number delivered."""

    delivered = 0
    for client_id, ws in external_client_sockets():
        try:
            await ws.send_text(json.dumps(payload, ensure_ascii=False))
            delivered += 1
        except Exception:
            state["clients"].pop(client_id, None)
            try:
                await ws.close(code=1011, reason="external connection send failed")
            except Exception:
                pass
    return delivered


def cleanup_pending_submissions() -> None:
    now_ts = time.time()
    for submission_id, record in list(state["pending_submissions"].items()):
        if now_ts - record.get("at", now_ts) > PENDING_SUBMISSION_TTL_S:
            state["pending_submissions"].pop(submission_id, None)


def _remove_pending_control(control_id: str, expected: dict | None = None) -> dict | None:
    current = state["pending_controls"].get(control_id)
    if current is None or (expected is not None and current is not expected):
        return None
    removed = state["pending_controls"].pop(control_id, None)
    task = state["control_expiry_tasks"].pop(control_id, None)
    if task is not None and task is not asyncio.current_task():
        task.cancel()
    return removed


async def _expire_control(control_id: str, record: dict) -> None:
    try:
        remaining = record["at"] + PENDING_CONTROL_TTL_S - time.time()
        if remaining > 0:
            await asyncio.sleep(remaining)
        _remove_pending_control(control_id, record)
    except asyncio.CancelledError:
        raise


def cleanup_pending_controls() -> None:
    now_ts = time.time()
    for control_id, record in list(state["pending_controls"].items()):
        if now_ts - record.get("at", now_ts) > PENDING_CONTROL_TTL_S:
            _remove_pending_control(control_id, record)


def _safe_control_request_id(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > MAX_CONTROL_ID_LENGTH:
        return None
    try:
        value.encode("utf-8")
    except UnicodeError:
        return None
    return value


def _control_error(request_id: str | None, code: str, message: str) -> dict:
    return {
        "type": "supervisor_control_result",
        "request_id": request_id,
        "success": False,
        "error": {"code": code, "message": message},
    }


def _new_control_id() -> str:
    return f"control-{uuid.uuid4().hex}"


def _build_control_frame(control_id: str, action: str, params: dict) -> dict:
    return {
        "type": "supervisor_control_request",
        "request_id": control_id,
        "action": action,
        "params": params,
    }


def _serialized_control_frame(action: str, params: dict) -> tuple[str, int]:
    control_id = _new_control_id()
    text = json.dumps(
        _build_control_frame(control_id, action, params),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return text, len(text.encode("utf-8"))


def _control_frame_fits(action: str, params: dict) -> bool:
    try:
        _, size = _serialized_control_frame(action, params)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return False
    return size <= MAX_CONTROL_FRAME_BYTES


def _canonical_control(action: str, params: dict) -> str:
    return json.dumps(
        {"action": action, "params": params},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


async def send_to_client(client_id: str, payload: dict) -> bool:
    ws = state["clients"].get(client_id)
    if ws is None:
        return False
    try:
        await ws.send_text(json.dumps(payload, ensure_ascii=False))
        return True
    except Exception:
        return False


async def handle_page_control(msg: dict, page_client_id: str) -> None:
    """Forward one validated page control to the current authenticated Core."""

    if page_client_id != state["page_client"]:
        return
    cleanup_pending_controls()
    raw_request_id = msg.get("request_id")
    request_id = _safe_control_request_id(raw_request_id)
    action = msg.get("action")
    params = msg.get("params")
    valid_fields = set(msg) == {"type", "request_id", "action", "params"}
    if (
        request_id is None
        or not valid_fields
        or action not in CONTROL_ACTIONS
        or not isinstance(params, dict)
    ):
        await send_to_client(
            page_client_id,
            _control_error(
                request_id,
                "INVALID_CONTROL_REQUEST",
                "invalid Supervisor control request",
            ),
        )
        return
    try:
        canonical = _canonical_control(action, params)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        await send_to_client(
            page_client_id,
            _control_error(
                request_id,
                "INVALID_CONTROL_REQUEST",
                "invalid Supervisor control request",
            ),
        )
        return

    existing = next(
        (
            record
            for record in state["pending_controls"].values()
            if record.get("page_client_id") == page_client_id
            and record.get("page_request_id") == request_id
        ),
        None,
    )
    if existing is not None:
        code = (
            "CONTROL_INFLIGHT"
            if existing.get("canonical_request") == canonical
            else "CONTROL_INFLIGHT_CONFLICT"
        )
        await send_to_client(
            page_client_id,
            _control_error(
                request_id,
                code,
                "Supervisor control request is already in flight",
            ),
        )
        return

    receiver_id = state["external_client"]
    receiver = state["clients"].get(receiver_id)
    if receiver_id is None or receiver is None:
        await send_to_client(
            page_client_id,
            _control_error(request_id, "CORE_UNAVAILABLE", "Core is not connected"),
        )
        return

    control_id = _new_control_id()
    forwarded = _build_control_frame(control_id, action, params)
    try:
        forwarded_text = json.dumps(
            forwarded,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        forwarded_size = len(forwarded_text.encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        await send_to_client(
            page_client_id,
            _control_error(
                request_id,
                "INVALID_CONTROL_REQUEST",
                "invalid Supervisor control request",
            ),
        )
        return
    if forwarded_size > MAX_CONTROL_FRAME_BYTES:
        await send_to_client(
            page_client_id,
            _control_error(
                request_id,
                "INVALID_CONTROL_REQUEST",
                "invalid Supervisor control request",
            ),
        )
        return
    record = {
        "page_request_id": request_id,
        "page_client_id": page_client_id,
        "receiver_client_id": receiver_id,
        "canonical_request": canonical,
        "at": time.time(),
    }
    state["pending_controls"][control_id] = record
    state["control_expiry_tasks"][control_id] = asyncio.create_task(
        _expire_control(control_id, record)
    )
    try:
        await receiver.send_text(forwarded_text)
    except Exception:
        _remove_pending_control(control_id, record)
        await send_to_client(
            page_client_id,
            _control_error(request_id, "CORE_UNAVAILABLE", "Core is not connected"),
        )


def _validated_core_control_result(msg: dict, request_id: str) -> dict:
    success = msg.get("success")
    if success is True and set(msg) == {"type", "request_id", "success", "data"} and isinstance(msg.get("data"), dict):
        result = {
            "type": "supervisor_control_result",
            "request_id": request_id,
            "success": True,
            "data": msg["data"],
        }
    elif success is False and set(msg) == {"type", "request_id", "success", "error"} and isinstance(msg.get("error"), dict):
        error = msg["error"]
        valid_error = (
            set(error) == {"code", "message"}
            and isinstance(error.get("code"), str)
            and bool(error["code"])
            and len(error["code"]) <= MAX_CONTROL_ID_LENGTH
            and isinstance(error.get("message"), str)
            and bool(error["message"])
            and len(error["message"]) <= MAX_CONTROL_ID_LENGTH
        )
        if valid_error:
            try:
                error["code"].encode("utf-8")
                error["message"].encode("utf-8")
            except UnicodeError:
                valid_error = False
        if not valid_error:
            result = _control_error(
                request_id,
                "CORE_PROTOCOL_ERROR",
                "Core returned an invalid control result",
            )
        else:
            result = {
                "type": "supervisor_control_result",
                "request_id": request_id,
                "success": False,
                "error": {"code": error["code"], "message": error["message"]},
            }
    else:
        result = _control_error(
            request_id,
            "CORE_PROTOCOL_ERROR",
            "Core returned an invalid control result",
        )
    try:
        encoded = json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return _control_error(
            request_id,
            "CORE_PROTOCOL_ERROR",
            "Core returned an invalid control result",
        )
    if len(encoded) > MAX_CONTROL_FRAME_BYTES:
        return _control_error(
            request_id,
            "CORE_PROTOCOL_ERROR",
            "Core returned an invalid control result",
        )
    return result


async def handle_core_control_result(msg: dict, client_id: str) -> None:
    control_id = msg.get("request_id")
    if not isinstance(control_id, str) or client_id != state["external_client"]:
        return
    record = state["pending_controls"].get(control_id)
    if (
        record is None
        or time.time() - record.get("at", 0) > PENDING_CONTROL_TTL_S
        or record.get("receiver_client_id") != client_id
        or record.get("page_client_id") != state["page_client"]
    ):
        if record is not None and time.time() - record.get("at", 0) > PENDING_CONTROL_TTL_S:
            _remove_pending_control(control_id, record)
        return
    _remove_pending_control(control_id, record)
    result = _validated_core_control_result(msg, record["page_request_id"])
    await send_to_client(record["page_client_id"], result)


async def handle_page_submission(msg: dict, page_client_id: str) -> None:
    """Page -> bridge: deliver one Adapter envelope to the sole external client."""

    if page_client_id != state["page_client"]:
        return
    cleanup_pending_submissions()
    envelope = msg.get("envelope")
    request_id = msg.get("request_id")
    submission_id = envelope.get("submission_id") if isinstance(envelope, dict) else None

    def ack(status: str, error: dict | None = None, duplicate: bool = False) -> dict:
        payload = {
            "type": "submission_ack",
            "request_id": request_id,
            "submission_id": submission_id,
            "status": status,
            "duplicate": duplicate,
        }
        if error is not None:
            payload["error"] = error
        return payload

    if not isinstance(envelope, dict) or not isinstance(submission_id, str):
        await send_to_page(ack("rejected", {"code": "INVALID_ENVELOPE",
                                            "message": "envelope must be an object with submission_id"}))
        return
    if request_id is None:
        await send_to_page(ack("rejected", {"code": "INVALID_REQUEST", "message": "request_id is required"}))
        return

    existing = state["pending_submissions"].get(submission_id)
    if existing is not None and existing.get("receiver_client_id") != state["external_client"]:
        state["pending_submissions"].pop(submission_id, None)
        existing = None
    if existing is not None and not existing.get("acked"):
        canonical = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if canonical(existing["envelope"]) != canonical(envelope):
            await send_to_page(ack("rejected", {"code": "SUBMISSION_INFLIGHT_CONFLICT",
                                               "message": "submission_id has a different Envelope awaiting acknowledgement"}))
            return
    state["pending_submissions"][submission_id] = {
        "request_id": request_id,
        "envelope": envelope,
        "at": time.time(),
        "acked": False,
        "receiver_client_id": state["external_client"],
        "page_client_id": page_client_id,
    }
    delivered = await broadcast_to_external({
        "type": "submission",
        "request_id": request_id,
        "envelope": envelope,
        "at": now(),
    })
    if delivered == 0:
        state["pending_submissions"].pop(submission_id, None)
        await send_to_page(ack("rejected", {"code": "NO_RECEIVER",
                                            "message": "no external receiver is connected to the bridge"}))


async def handle_page_endpoint_lookup(msg: dict) -> None:
    session_id = msg.get("session_id")
    request_id = msg.get("request_id")
    endpoint_id = endpoint_for_session(session_id) if isinstance(session_id, str) else None
    await send_to_page({
        "type": "endpoint_lookup_result",
        "request_id": request_id,
        "session_id": session_id,
        "found": endpoint_id is not None,
        "endpoint_id": endpoint_id,
    })


async def handle_submission_ack(msg: dict, client_id: str) -> None:
    """Route acknowledgement only from the connection that received the submission."""

    submission_id = msg.get("submission_id")
    if not isinstance(submission_id, str) or client_id != state["external_client"]:
        return
    record = state["pending_submissions"].get(submission_id)
    if record is None or record.get("receiver_client_id") != client_id or record.get("page_client_id") != state["page_client"]:
        return
    if "request_id" in msg and (type(msg["request_id"]) is not type(record["request_id"]) or msg["request_id"] != record["request_id"]):
        return
    if record.get("acked"):
        return
    record["acked"] = True
    await send_to_page({
        "type": "submission_ack",
        "request_id": record.get("request_id"),
        "submission_id": submission_id,
        "status": msg.get("status"),
        "duplicate": msg.get("duplicate") is True,
        "error": msg.get("error"),
    })


async def send_to_page(payload: dict) -> bool:
    page_id = state["page_client"]
    if page_id is None:
        return False
    ws = state["clients"].get(page_id)
    if ws is None:
        return False
    try:
        await ws.send_text(json.dumps(payload, ensure_ascii=False))
        return True
    except Exception:
        return False


async def ws_common(ws: WebSocket, is_page: bool) -> None:
    token = ws.query_params.get("token")
    if token != DEV_TOKEN:
        await ws.close(code=4401, reason="invalid token")
        return
    origin = ws.headers.get("origin")
    if not origin_allowed(origin):
        await ws.close(code=4403, reason="origin not allowed")
        return
    client_id = f"client-{uuid.uuid4().hex}"
    report_sends: set[asyncio.Task] = set()

    async def relay_report(request: dict) -> None:
        result = await relay_to_page(request)
        if result is None:
            result = {"id": request["id"], "type": "result", "success": False,
                      "error": {"code": "CONTENT_SCRIPT_UNAVAILABLE", "message": "bridge page unreachable"}}
        elif not isinstance(result, dict) or result.get("id") != request["id"]:
            result = {"id": request["id"], "type": "result", "success": False,
                      "error": {"code": "REPORT_SEND_UNKNOWN", "message": "browser receipt did not match the request"}}
        await send_to_client(client_id, result)

    if not is_page:
        if state["external_client"] is not None:
            await ws.accept()
            await ws.close(code=4409, reason="external Core connection already active")
            return
        # Reserve before awaiting accept; concurrent handshakes cannot both claim it.
        state["external_client"] = client_id
    try:
        await ws.accept()
        state["clients"][client_id] = ws
        if is_page:
            state["page_client"] = client_id
        await ws.send_text(json.dumps({"type": "welcome", "client_id": client_id, "at": now()}))
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_text(json.dumps({"id": None, "type": "result", "success": False,
                                               "error": {"code": "INVALID_REQUEST", "message": "invalid JSON"}}))
                continue

            if is_page:
                mtype = msg.get("type")
                if mtype == "bridge_result":
                    request_id = msg.get("request_id")
                    fut = state["pending"].pop(request_id, None)
                    if fut and not fut.done():
                        fut.set_result(msg.get("result"))
                elif mtype == "adapter_submission":
                    await handle_page_submission(msg, client_id)
                elif mtype == "endpoint_lookup":
                    await handle_page_endpoint_lookup(msg)
                elif mtype == "supervisor_control_request":
                    await handle_page_control(msg, client_id)
                continue  # page client does not issue request/response calls itself

            # External client: acknowledgements/results for page-originated work.
            if msg.get("type") == "submission_ack":
                await handle_submission_ack(msg, client_id)
                continue
            if msg.get("type") == "supervisor_control_result":
                await handle_core_control_result(msg, client_id)
                continue

            # External client request
            request_id = msg.get("id")
            if not isinstance(request_id, str) or not request_id:
                await ws.send_text(json.dumps({"id": None, "type": "result", "success": False,
                                               "error": {"code": "INVALID_REQUEST", "message": "missing id"}}))
                continue
            mtype = msg.get("type")
            if mtype not in ("list_sessions", "get_snapshot", "get_generation",
                             "get_composer", "prepare_message", "clear_composer", "send_message",
                             "send_message_to_endpoint", "debug_action_bars", "click_submit_button",
                             "reload_tab", "reload_extension", "reload_tabs"):
                await ws.send_text(json.dumps({"id": request_id, "type": "result", "success": False,
                                               "error": {"code": "INVALID_REQUEST", "message": f"unknown type {mtype}"}}))
                continue
            if mtype in ("get_snapshot", "get_generation", "get_composer", "prepare_message",
                         "clear_composer", "send_message", "debug_action_bars", "click_submit_button") \
                    and not msg.get("session_id") and msg.get("tab_id") is None:
                await ws.send_text(json.dumps({"id": request_id, "type": "result", "success": False,
                                               "error": {"code": "INVALID_REQUEST", "message": "session_id or tab_id required"}}))
                continue
            if mtype == "click_submit_button" and not isinstance(msg.get("message_id"), str):
                await ws.send_text(json.dumps({"id": request_id, "type": "result", "success": False,
                                               "error": {"code": "INVALID_REQUEST", "message": "message_id is required"}}))
                continue
            if mtype == "reload_tab" and msg.get("tab_id") is None:
                await ws.send_text(json.dumps({"id": request_id, "type": "result", "success": False,
                                               "error": {"code": "INVALID_REQUEST", "message": "tab_id is required"}}))
                continue
            if mtype in ("prepare_message", "send_message_to_endpoint"):
                text = msg.get("text")
                if not isinstance(text, str) or not text:
                    await ws.send_text(json.dumps({"id": request_id, "type": "result", "success": False,
                                                   "error": {"code": "INVALID_REQUEST", "message": "text is required and must be non-empty"}}))
                    continue
            if mtype == "send_message_to_endpoint":
                endpoint_id = msg.get("endpoint_id")
                if not isinstance(endpoint_id, str) or not endpoint_id:
                    await ws.send_text(json.dumps({"id": request_id, "type": "result", "success": False,
                                                   "error": {"code": "INVALID_REQUEST", "message": "endpoint_id is required"}}))
                    continue
                binding = session_for_endpoint(endpoint_id)
                if binding is None:
                    await ws.send_text(json.dumps({"id": request_id, "type": "result", "success": False,
                                                   "error": {"code": "ENDPOINT_NOT_FOUND",
                                                             "message": f"no endpoint binding for {endpoint_id}"}}))
                    continue
                msg = dict(msg)
                if "expected_native_session_id" in msg:
                    expected = msg.pop("expected_native_session_id")
                    if (not isinstance(expected, str) or not expected
                            or binding["provider"] != "chatgpt_web" or expected != binding["native_session_id"]):
                        await send_to_client(client_id, {
                            "id": request_id, "type": "result", "success": False,
                            "error": {"code": "ENDPOINT_SESSION_MISMATCH", "message": "Endpoint no longer matches the original session"},
                        })
                        continue
                    msg["session_id"] = expected
                    msg.pop("tab_id", None)
                    msg["force"] = False
                    if state["page_client"] is None:
                        await send_to_client(client_id, {
                            "id": request_id, "type": "result", "success": False,
                            "error": {"code": "REPORT_PAGE_UNAVAILABLE", "message": "no browser relay is connected"},
                        })
                        continue
                    if report_sends:
                        await send_to_client(client_id, {
                            "id": request_id, "type": "result", "success": False,
                            "error": {"code": "REPORT_SEND_BUSY", "message": "another report send is still in progress"},
                        })
                        continue
                    # Verification can take seconds. Keep reading ingress ACKs and review replies.
                    job = asyncio.create_task(relay_report(msg))
                    report_sends.add(job)
                    job.add_done_callback(report_sends.discard)
                    continue
                msg["session_id"] = binding["native_session_id"]
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
        pending_sends = tuple(report_sends)
        for job in pending_sends:
            job.cancel()
        if pending_sends:
            await asyncio.gather(*pending_sends, return_exceptions=True)
        state["clients"].pop(client_id, None)
        if state["page_client"] == client_id:
            state["page_client"] = None
        if state["external_client"] == client_id:
            state["external_client"] = None
        for control_id, record in list(state["pending_controls"].items()):
            if client_id in (
                record.get("page_client_id"),
                record.get("receiver_client_id"),
            ):
                _remove_pending_control(control_id, record)


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
    ap.add_argument("--registry", help="override the CF-CORE-001 endpoint registry path")
    args = ap.parse_args()
    if args.registry:
        os.environ["CONTEXTFLOW_ENDPOINT_REGISTRY"] = args.registry
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
