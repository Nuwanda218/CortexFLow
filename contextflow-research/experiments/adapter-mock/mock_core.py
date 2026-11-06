#!/usr/bin/env python3
"""CF-ADAPTER-001 Mock Core / external receiver (lightweight Python test script).

This is NOT the ContextFlow Core.  It only:

* receives submission envelopes from the bridge;
* validates them and enforces ``submission_id`` idempotency;
* stores them in a JSON file for inspection;
* returns a short acknowledgement;
* can initiate one reverse-send test by Endpoint ID.

Modes
-----
Receiver daemon (keep running while clicking paper planes):

    python mock_core.py --store _artifacts/cfadapter001-submissions.json

Reverse send test (sends one message into the ChatGPT session bound to an
Endpoint ID, then prints the verified result):

    python mock_core.py --send-endpoint cfep_... --text "ContextFlow test message"

Inspect what was received:

    python mock_core.py --store _artifacts/cfadapter001-submissions.json --list

Test knobs
----------
    --ack-delay SECONDS   delay the acknowledgement (simulates a slow receiver)
    --drop-first N        do not acknowledge the first N submissions (simulates
                          a lost acknowledgement; the Adapter must show "unknown"
                          and retry with the SAME submission_id)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contextflow_core.submission import MockCoreReceiver  # noqa: E402

DEFAULT_URI = "ws://127.0.0.1:8766/ws?token=dev-token-cf-exp-002"
DEFAULT_ORIGIN = "http://127.0.0.1:8766"


def log(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


async def run_receiver(args) -> int:
    receiver = MockCoreReceiver(args.store)
    dropped = 0
    async with websockets.connect(args.uri, origin=args.origin, max_size=8 * 1024 * 1024) as ws:
        log("connected", store=str(args.store), existing=receiver.count())
        async for raw in ws:
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if message.get("type") == "welcome":
                continue
            if message.get("type") != "submission":
                continue
            envelope = message.get("envelope")
            submission_id = envelope.get("submission_id") if isinstance(envelope, dict) else None
            if dropped < args.drop_first:
                dropped += 1
                log("submission_dropped_for_test", submission_id=submission_id, dropped=dropped)
                continue
            if args.ack_delay > 0:
                await asyncio.sleep(args.ack_delay)
            ack = receiver.receive(envelope)
            log(
                "submission",
                submission_id=submission_id,
                status=ack.get("status"),
                duplicate=ack.get("duplicate", False),
                error=ack.get("error"),
                stored=receiver.count(),
                source=(envelope or {}).get("source") if isinstance(envelope, dict) else None,
                text_length=len(((envelope or {}).get("content") or {}).get("text") or ""),
            )
            await ws.send(json.dumps({
                "type": "submission_ack",
                "submission_id": submission_id,
                "status": ack.get("status"),
                "duplicate": ack.get("duplicate", False),
                "error": ack.get("error"),
            }, ensure_ascii=False))
            if args.once and ack.get("status") == "accepted":
                return 0
    return 0


async def run_send(args) -> int:
    request_id = "send-" + uuid.uuid4().hex[:8]
    async with websockets.connect(args.uri, origin=args.origin, max_size=8 * 1024 * 1024) as ws:
        log("connected", mode="send", endpoint_id=args.send_endpoint)
        await ws.send(json.dumps({
            "id": request_id,
            "type": "send_message_to_endpoint",
            "endpoint_id": args.send_endpoint,
            "text": args.text,
            "force": args.force,
        }, ensure_ascii=False))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=args.timeout)
            message = json.loads(raw)
            if message.get("type") == "welcome":
                continue
            if message.get("type") == "submission":
                # A paper-plane submission may arrive while we are here; ack it
                # so the browser does not show a false timeout.
                envelope = message.get("envelope") or {}
                receiver = MockCoreReceiver(args.store)
                ack = receiver.receive(envelope)
                await ws.send(json.dumps({
                    "type": "submission_ack",
                    "submission_id": envelope.get("submission_id"),
                    "status": ack.get("status"),
                    "duplicate": ack.get("duplicate", False),
                    "error": ack.get("error"),
                }, ensure_ascii=False))
                log("submission_acked_during_send", submission_id=envelope.get("submission_id"),
                    status=ack.get("status"))
                continue
            if message.get("id") != request_id:
                continue
            log("send_result", result=message)
            if not message.get("success"):
                return 1
            data = message.get("data") or {}
            return 0 if data.get("verified") else 2


def run_list(args) -> int:
    receiver = MockCoreReceiver(args.store)
    records = receiver.all()
    print(json.dumps({
        "store": str(args.store),
        "count": len(records),
        "submissions": [
            {
                "submission_id": record["submission_id"],
                "received_at": record.get("received_at"),
                "source": record["envelope"].get("source"),
                "text_length": len(record["envelope"].get("content", {}).get("text", "")),
                "text_preview": record["envelope"].get("content", {}).get("text", "")[:120],
            }
            for record in records
        ],
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="CF-ADAPTER-001 Mock Core / external receiver")
    parser.add_argument("--store", default=str(ROOT / "_artifacts" / "cfadapter001-submissions.json"))
    parser.add_argument("--uri", default=DEFAULT_URI)
    parser.add_argument("--origin", default=DEFAULT_ORIGIN)
    parser.add_argument("--ack-delay", type=float, default=0.0)
    parser.add_argument("--drop-first", type=int, default=0)
    parser.add_argument("--once", action="store_true", help="exit after the first accepted submission")
    parser.add_argument("--list", action="store_true", help="print stored submissions and exit")
    parser.add_argument("--send-endpoint", help="reverse-send test: target Endpoint ID")
    parser.add_argument("--text", help="reverse-send test: message text")
    parser.add_argument("--force", action="store_true", help="reverse-send test: replace an existing draft")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    if args.list:
        return run_list(args)
    if args.send_endpoint:
        if not args.text:
            parser.error("--text is required with --send-endpoint")
        return asyncio.run(run_send(args))
    return asyncio.run(run_receiver(args))


if __name__ == "__main__":
    sys.exit(main())
