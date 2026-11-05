#!/usr/bin/env python3
"""CF-DEV-004 (experiment A): prepare_message — fill the composer, never send.

API (bridge JSON over WS):
  {"type":"get_composer","session_id":"<uuid>"}
  {"type":"prepare_message","session_id":"<uuid>","text":"...","force":false}
  {"type":"clear_composer","session_id":"<uuid>"}   # test/cleanup utility

CLI:
  python cfdev004_prepare.py --session <uuid> --read
  python cfdev004_prepare.py --session <uuid> --text "hello"
  python cfdev004_prepare.py --session <uuid> --file msg.txt [--force]
  python cfdev004_prepare.py --session <uuid> --clear
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cfdev003_wait import BridgeClient, BridgeError  # noqa: E402


async def get_composer(client: BridgeClient, session_id: str, tab_id: int | None = None) -> dict:
    payload = {"type": "get_composer", "session_id": session_id}
    if tab_id is not None:
        payload["tab_id"] = tab_id
    return await client._call(payload)


async def prepare_message(client: BridgeClient, session_id: str, text: str,
                          force: bool = False, tab_id: int | None = None) -> dict:
    payload = {"type": "prepare_message", "session_id": session_id, "text": text, "force": force}
    if tab_id is not None:
        payload["tab_id"] = tab_id
    return await client._call(payload)


async def clear_composer(client: BridgeClient, session_id: str, tab_id: int | None = None) -> dict:
    payload = {"type": "clear_composer", "session_id": session_id}
    if tab_id is not None:
        payload["tab_id"] = tab_id
    return await client._call(payload)


async def _cli() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--text")
    ap.add_argument("--file")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--read", action="store_true")
    ap.add_argument("--clear", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    client = BridgeClient()
    await client.connect()
    try:
        if args.read:
            data = await get_composer(client, args.session)
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0
        if args.clear:
            data = await clear_composer(client, args.session)
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0
        text = args.text
        if args.file:
            text = Path(args.file).read_text(encoding="utf-8")
        if text is None:
            ap.error("--text or --file is required (or use --read/--clear)")
        data = await prepare_message(client, args.session, text, force=args.force)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(f"[prepare] prepared={data.get('prepared')} verified={data.get('verified')} "
                  f"method={data.get('method')} draft_replaced={data.get('draft_replaced')} "
                  f"len={data.get('composer_text_length')}")
        return 0
    except BridgeError as exc:
        print(f"[prepare] FAILED: {exc.code}: {exc.message}")
        return 1
    finally:
        await client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(_cli()))
