#!/usr/bin/env python3
"""CF-DEV-004 experiment B: prepare -> manual send -> wait_for_reply.

Flow:
  1. baseline snapshot (message ids)
  2. prepare_message: fill the composer (never sends)
  3. wait until the user manually clicks Send (new user message appears)
  4. wait_for_reply: capture the new assistant reply

Usage:
  python cfdev004_expb.py --session <uuid> --text "..." [--timeout 180]
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
from cfdev004_prepare import prepare_message  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


async def wait_for_user_message(client: BridgeClient, session_id: str, baseline_ids: set[str],
                                timeout: float = 300.0, poll: float = 0.8) -> dict | None:
    """Wait until a NEW user message appears (i.e. the user clicked Send)."""
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        try:
            snap = await client.get_snapshot(session_id)
        except BridgeError:
            await asyncio.sleep(poll)
            continue
        messages, _ = _snap_parts(snap)
        new_users = [m for m in messages if m.get("role") == "user" and not (_ids_of(m) & baseline_ids)]
        if new_users:
            return {"snapshot": snap, "user_message": new_users[-1]}
        await asyncio.sleep(poll)
    return None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--poll", type=float, default=0.8)
    args = ap.parse_args()

    client = BridgeClient()
    await client.connect()
    try:
        # 1) baseline
        base = await client.get_snapshot(args.session)
        base_messages, _ = _snap_parts(base)
        baseline_ids: set[str] = set()
        for m in base_messages:
            baseline_ids |= _ids_of(m)
        print(f"[B] baseline: {len(base_messages)} messages, {len(baseline_ids)} ids", flush=True)

        # 2) prepare (fill only)
        prep = await prepare_message(client, args.session, args.text)
        print(f"[B] prepared: verified={prep.get('verified')} method={prep.get('method')} "
              f"len={prep.get('composer_text_length')} sent={prep.get('sent')}", flush=True)
        print("[B] >>> 请在浏览器里点击「发送」按钮（脚本不会发送）", flush=True)

        # 3) wait for manual send
        sent = await wait_for_user_message(client, args.session, baseline_ids,
                                           timeout=args.timeout, poll=args.poll)
        if sent is None:
            print("[B] FAILED: no user message appeared (send not clicked?)", flush=True)
            return 1
        um = sent["user_message"]
        print(f"[B] send detected: user message id={um.get('message_id')} order={um.get('order')} "
              f"len={um.get('text_length')}", flush=True)

        # 4) wait for the reply (baseline = snapshot that includes our user message)
        result = await wait_for_reply(client, args.session, timeout=args.timeout,
                                      poll=args.poll, baseline=sent["snapshot"])
        if not result.get("ok"):
            print(f"[B] FAILED waiting reply: {result['error']['code']}: {result['error']['message']}", flush=True)
            return 1
        r = result["reply"]
        print(f"[B] reply captured: id={r['message_id']} order={r['order']} len={r['text_length']} "
              f"waited={result['waited_ms']}ms polls={result['polls']}", flush=True)
        print("----- reply -----", flush=True)
        print(r["content"], flush=True)
        print("----- end -----", flush=True)
        return 0
    except BridgeError as exc:
        print(f"[B] FAILED: {exc.code}: {exc.message}", flush=True)
        return 1
    finally:
        await client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
