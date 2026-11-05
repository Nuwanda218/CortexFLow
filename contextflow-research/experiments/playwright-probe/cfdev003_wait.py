#!/usr/bin/env python3
"""CF-DEV-003: wait_for_reply on top of the realtime snapshot API.

Design (per Coordinator review):
  - baseline message-id set captured BEFORE waiting
  - poll the lightweight get_generation endpoint (no DOM body parsing)
  - only when state == "completed" AND a NEW assistant message (id not in the
    baseline) exists do we fetch the full snapshot and return it
  - a page that is already "completed" at baseline must NOT return the old reply
  - timeout returns TIMEOUT with the last observed state (never a stale reply)
  - session change during the wait returns SESSION_CHANGED

CLI:
  python cfdev003_wait.py --session <uuid> [--timeout 120] [--poll 0.8] [--json]
  python cfdev003_wait.py --list
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid

import websockets

BRIDGE_URI = "ws://127.0.0.1:8766/ws?token=dev-token-cf-exp-002"
BRIDGE_ORIGIN = "http://127.0.0.1:8766"


class BridgeError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class BridgeClient:
    """Minimal persistent client for the local bridge (request/response by id)."""

    def __init__(self, uri: str = BRIDGE_URI, origin: str = BRIDGE_ORIGIN):
        self.uri = uri
        self.origin = origin
        self._ws = None
        self._reader = None
        self._pending: dict[str, asyncio.Future] = {}
        self._closed = False

    async def connect(self) -> None:
        self._ws = await websockets.connect(self.uri, origin=self.origin)
        self._reader = asyncio.create_task(self._read_loop())
        # consume welcome
        await asyncio.sleep(0)

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "welcome":
                    continue
                rid = msg.get("id")
                fut = self._pending.pop(rid, None)
                if fut and not fut.done():
                    fut.set_result(msg)
        except Exception:
            pass
        finally:
            self._closed = True
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(BridgeError("BRIDGE_DISCONNECTED", "bridge connection closed"))

    async def request(self, payload: dict, timeout: float = 20.0) -> dict:
        if self._ws is None or self._closed:
            raise BridgeError("BRIDGE_DISCONNECTED", "not connected")
        rid = payload.setdefault("id", f"req-{uuid.uuid4().hex[:8]}")
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._ws.send(json.dumps(payload))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(rid, None)

    async def _call(self, payload: dict, timeout: float = 20.0) -> dict:
        resp = await self.request(payload, timeout=timeout)
        if not resp.get("success"):
            err = resp.get("error") or {}
            raise BridgeError(err.get("code", "UNKNOWN"), err.get("message", ""))
        return resp.get("data") or {}

    async def list_sessions(self) -> list[dict]:
        data = await self._call({"type": "list_sessions"})
        return data.get("sessions", [])

    async def get_snapshot(self, session_id: str, tab_id: int | None = None) -> dict:
        payload = {"type": "get_snapshot", "session_id": session_id}
        if tab_id is not None:
            payload["tab_id"] = tab_id
        return await self._call(payload)

    async def get_generation(self, session_id: str, tab_id: int | None = None) -> dict:
        payload = {"type": "get_generation", "session_id": session_id}
        if tab_id is not None:
            payload["tab_id"] = tab_id
        return await self._call(payload)

    async def close(self) -> None:
        self._closed = True
        if self._reader:
            self._reader.cancel()
        if self._ws:
            await self._ws.close()


def _ids_of(msg: dict) -> set[str]:
    ids = set(msg.get("message_ids") or [])
    if msg.get("message_id"):
        ids.add(msg["message_id"])
    return ids


def _snap_parts(data: dict) -> tuple[list[dict], int | None]:
    """get_snapshot returns {tab_id, captured_at, snapshot:{...}}; unwrap it."""
    if not isinstance(data, dict):
        return [], None
    snap = data.get("snapshot")
    if isinstance(snap, dict):
        return snap.get("messages") or [], data.get("tab_id")
    return data.get("messages") or [], data.get("tab_id")


async def _session_changed_or_not_found(client: BridgeClient, target_tab_id: int | None, expected: str) -> str:
    """Distinguish 'tab navigated to another session' from 'tab gone'."""
    try:
        sessions = await client.list_sessions()
    except BridgeError:
        return "SESSION_NOT_FOUND"
    for s in sessions:
        if target_tab_id is not None and s.get("tab_id") == target_tab_id:
            sid = s.get("session_id")
            if sid and sid != expected:
                return "SESSION_CHANGED"
            return "SESSION_NOT_FOUND"
    return "SESSION_NOT_FOUND"


async def wait_for_reply(
    client: BridgeClient,
    session_id: str,
    timeout: float = 120.0,
    poll: float = 0.8,
    baseline: dict | None = None,
) -> dict:
    """Wait for a NEW assistant message to appear and complete.

    Returns dict:
      ok=True  -> {reply, new_assistant_ids, multiple, waited_ms, polls, transitions}
      ok=False -> {error:{code,message}, last_state, transitions, waited_ms, polls}
    """
    started = time.monotonic()
    transitions: list[dict] = []
    polls = 0

    # 1) baseline
    try:
        base = baseline if baseline is not None else await client.get_snapshot(session_id)
    except BridgeError as exc:
        return {"ok": False, "error": {"code": exc.code, "message": exc.message},
                "waited_ms": 0, "polls": 0, "transitions": []}
    base_messages, target_tab_id = _snap_parts(base)
    baseline_ids: set[str] = set()
    for m in base_messages:
        baseline_ids |= _ids_of(m)

    last_state = None
    deadline = started + timeout

    while time.monotonic() < deadline:
        polls += 1
        try:
            gen = await client.get_generation(session_id)
        except BridgeError as exc:
            code = exc.code
            if code == "SESSION_NOT_FOUND":
                code = await _session_changed_or_not_found(client, target_tab_id, session_id)
            return {"ok": False, "error": {"code": code, "message": exc.message},
                    "last_state": last_state, "transitions": transitions,
                    "waited_ms": int((time.monotonic() - started) * 1000), "polls": polls}

        if gen.get("session_id") != session_id:
            return {"ok": False,
                    "error": {"code": "SESSION_CHANGED",
                              "message": f"session changed to {gen.get('session_id')}"},
                    "last_state": last_state, "transitions": transitions,
                    "waited_ms": int((time.monotonic() - started) * 1000), "polls": polls}

        state = (gen.get("generation") or {}).get("state")
        if state != last_state:
            transitions.append({"at_ms": int((time.monotonic() - started) * 1000), "state": state})
            last_state = state

        if state == "completed":
            # Stale-completed trap: only return if a NEW assistant message exists.
            try:
                snap = await client.get_snapshot(session_id)
            except BridgeError as exc:
                code = exc.code
                if code == "SESSION_NOT_FOUND":
                    code = await _session_changed_or_not_found(client, target_tab_id, session_id)
                return {"ok": False, "error": {"code": code, "message": exc.message},
                        "last_state": last_state, "transitions": transitions,
                        "waited_ms": int((time.monotonic() - started) * 1000), "polls": polls}
            snap_messages, _ = _snap_parts(snap)
            new_assistants = [
                m for m in snap_messages
                if m.get("role") == "assistant" and not (_ids_of(m) & baseline_ids)
            ]
            complete = [m for m in new_assistants if m.get("status") == "complete"]
            if complete:
                reply = complete[-1]
                reply_id = reply.get("message_id") or (reply.get("message_ids") or [None])[0]
                return {
                    "ok": True,
                    "reply": {
                        "message_id": reply_id,
                        "message_ids": reply.get("message_ids"),
                        "order": reply.get("order"),
                        "content": reply.get("content"),
                        "text_length": reply.get("text_length"),
                        "status": reply.get("status"),
                    },
                    "new_assistant_ids": [m.get("message_id") or (m.get("message_ids") or [None])[0] for m in new_assistants],
                    "multiple": len(new_assistants) > 1,
                    "waited_ms": int((time.monotonic() - started) * 1000),
                    "polls": polls,
                    "transitions": transitions,
                }
            # new assistant exists but still in_progress -> keep waiting
        await asyncio.sleep(poll)

    return {"ok": False,
            "error": {"code": "TIMEOUT", "message": f"no new completed reply within {timeout}s"},
            "last_state": last_state, "transitions": transitions,
            "waited_ms": int((time.monotonic() - started) * 1000), "polls": polls}


async def _cli() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", help="target session id")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--poll", type=float, default=0.8)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    client = BridgeClient()
    await client.connect()
    try:
        if args.list:
            sessions = await client.list_sessions()
            for s in sessions:
                print(f"tab={s.get('tab_id')} session={s.get('session_id')} kind={s.get('kind')} ready={s.get('ready')}")
            return 0
        if not args.session:
            ap.error("--session is required unless --list")
        print(f"[wait] baseline for {args.session} …", flush=True)
        result = await wait_for_reply(client, args.session, timeout=args.timeout, poll=args.poll)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif result["ok"]:
            r = result["reply"]
            print(f"[wait] OK in {result['waited_ms']}ms ({result['polls']} polls)")
            print(f"[wait] message_id={r['message_id']} order={r['order']} len={r['text_length']}")
            print("----- reply -----")
            print(r["content"])
        else:
            print(f"[wait] FAILED: {result['error']['code']}: {result['error']['message']}")
            print(f"[wait] last_state={result.get('last_state')} transitions={result.get('transitions')}")
        return 0 if result["ok"] else 1
    finally:
        await client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(_cli()))
