#!/usr/bin/env python3
"""CF-DEV-004 experiment C: full loop — prepare -> send -> verify -> wait_for_reply.

This is the end-to-end "talk to ChatGPT" primitive:
  chat(client, session_id, text) -> reply

Real-world note: ChatGPT may reload the page immediately after a send, which
kills the content script and drops the send response. Therefore the send is
verified by *reading a fresh snapshot* (new user message with our text), not by
trusting the in-page response.

Safety:
  - prepare is verified before sending;
  - send is non-idempotent: if the message never appears, we fail with
    SEND_UNVERIFIED and NEVER retry automatically;
  - session is re-checked at every step;
  - baseline snapshot is taken before sending so the reply is correctly associated.

CLI:
  python cfdev004_chat.py --session <uuid> --text "..." [--timeout 240]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cfdev003_wait import BridgeClient, BridgeError, wait_for_reply, _snap_parts, _ids_of  # noqa: E402
from cfdev004_prepare import prepare_message, send_message  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _norm(s: str) -> str:
    return " ".join(str(s or "").split())


async def wait_for_user_message(client: BridgeClient, session_id: str, text: str,
                                baseline_ids: set[str], timeout: float = 40.0,
                                poll: float = 0.8) -> dict | None:
    """Wait until a NEW user message containing `text` appears (send verified)."""
    started = time.monotonic()
    target = _norm(text)
    while time.monotonic() - started < timeout:
        try:
            snap = await client.get_snapshot(session_id)
        except BridgeError as exc:
            if exc.code in ("SESSION_NOT_FOUND", "CONTENT_SCRIPT_UNAVAILABLE", "READ_TIMEOUT"):
                await asyncio.sleep(poll)
                continue
            raise
        messages, _ = _snap_parts(snap)
        for m in messages:
            if m.get("role") != "user":
                continue
            if _ids_of(m) & baseline_ids:
                continue
            if target in _norm(m.get("content") or ""):
                return {"snapshot": snap, "user_message": m}
        await asyncio.sleep(poll)
    return None


async def chat(client: BridgeClient, session_id: str, text: str,
               timeout: float = 240.0, poll: float = 0.8) -> dict:
    """Fill, send, verify by snapshot, and wait for the reply."""
    base = await client.get_snapshot(session_id)
    base_messages, _ = _snap_parts(base)
    baseline_ids: set[str] = set()
    for m in base_messages:
        baseline_ids |= _ids_of(m)

    prep = await prepare_message(client, session_id, text)
    if not prep.get("verified"):
        return {"ok": False, "stage": "prepare",
                "error": {"code": "PREPARE_UNVERIFIED", "message": "composer not verified"}}

    # Send. A page reload right after send can drop the response — that is not
    # a failure; we verify by reading the conversation below.
    send: dict
    try:
        send = await send_message(client, session_id)
    except BridgeError as exc:
        if exc.code not in ("READ_TIMEOUT", "CONTENT_SCRIPT_UNAVAILABLE", "SESSION_NOT_FOUND"):
            return {"ok": False, "stage": "send", "error": {"code": exc.code, "message": exc.message}}
        send = {"dispatched": True, "verified": False, "note": exc.code}

    sent = await wait_for_user_message(client, session_id, text, baseline_ids, timeout=40.0, poll=poll)
    if sent is None:
        return {"ok": False, "stage": "send_verify", "send": send,
                "error": {"code": "SEND_UNVERIFIED",
                          "message": "message did not appear in the conversation; NOT retrying"}}
    send["verified"] = True
    send["user_message_id"] = sent["user_message"].get("message_id")

    result = await wait_for_reply(client, session_id, timeout=timeout, poll=poll, baseline=sent["snapshot"])
    if not result.get("ok"):
        return {"ok": False, "stage": "wait", "send": send, "error": result["error"]}
    return {"ok": True, "send": send, "reply": result["reply"],
            "waited_ms": result["waited_ms"], "polls": result["polls"]}


async def _cli() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--timeout", type=float, default=240.0)
    ap.add_argument("--poll", type=float, default=0.8)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    client = BridgeClient()
    await client.connect()
    try:
        print(f"[chat] sending to {args.session} …", flush=True)
        result = await chat(client, args.session, args.text, timeout=args.timeout, poll=args.poll)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif result["ok"]:
            s = result["send"]
            r = result["reply"]
            print(f"[chat] sent via {s.get('method', s.get('note', 'unknown'))} "
                  f"(verified by snapshot, user_msg={s.get('user_message_id')})", flush=True)
            print(f"[chat] reply id={r['message_id']} order={r['order']} len={r['text_length']} "
                  f"waited={result['waited_ms']}ms", flush=True)
            print("----- reply -----", flush=True)
            print(r["content"], flush=True)
            print("----- end -----", flush=True)
        else:
            print(f"[chat] FAILED at {result.get('stage')}: {result['error']['code']}: {result['error']['message']}", flush=True)
        return 0 if result["ok"] else 1
    except BridgeError as exc:
        print(f"[chat] FAILED: {exc.code}: {exc.message}", flush=True)
        return 1
    finally:
        await client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(_cli()))
