#!/usr/bin/env python3
"""Dev utility: reload the unpacked extension and/or the ChatGPT tabs via bridge.

  python cfdev004_reload.py --extension   # chrome.runtime.reload() (new code from disk)
  python cfdev004_reload.py --tabs        # reload all candidate ChatGPT tabs
  python cfdev004_reload.py --all
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cfdev003_wait import BridgeClient, BridgeError  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extension", action="store_true")
    ap.add_argument("--tabs", action="store_true")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    if not (args.extension or args.tabs or args.all):
        ap.error("choose --extension, --tabs or --all")

    client = BridgeClient()
    await client.connect()
    try:
        if args.extension or args.all:
            try:
                data = await client._call({"type": "reload_extension"}, timeout=10)
                print(f"[reload] extension: {data}")
            except BridgeError as exc:
                # The extension may drop the connection while reloading; that's expected.
                print(f"[reload] extension reload triggered (connection dropped: {exc.code})")
            await asyncio.sleep(2.0)
            # reconnect (the bridge page may reload itself after the extension restart)
            for attempt in range(10):
                try:
                    await client.close()
                    client = BridgeClient()
                    await client.connect()
                    await client._call({"type": "list_sessions"}, timeout=10)
                    print("[reload] bridge reconnected")
                    break
                except Exception:
                    await asyncio.sleep(1.5)
        if args.tabs or args.all:
            data = await client._call({"type": "reload_tabs"}, timeout=20)
            print(f"[reload] tabs: {data}")
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
