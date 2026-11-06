#!/usr/bin/env python3
"""CF-ADAPTER-001 standalone protocol test (no browser, no UI).

Simulates the browser bridge page and the external receiver against the real
``bridge_server.py``:

    fake page client --WS /ws_page--> bridge --WS /ws--> mock_core.py
                                                          (MockCoreReceiver)

Checks:
  1. endpoint lookup by native session id;
  2. envelope delivery and accepted acknowledgement;
  3. duplicate submission with the same submission_id is idempotent;
  4. duplicate submission with different content is reported as a conflict;
  5. reverse send by Endpoint ID is routed to the page with the right session.

Run:
    python experiments/adapter-mock/adapter_protocol_test.py
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contextflow_core.registry import EndpointRegistry  # noqa: E402
from contextflow_core.submission import MockCoreReceiver  # noqa: E402

BRIDGE = ROOT / "contextflow-extension" / "bridge_server.py"
MOCK_CORE = Path(__file__).resolve().parent / "mock_core.py"
TOKEN = "dev-token-cf-exp-002"
SESSION = "6aeb4db5-d930-83ee-8b14-0cf74ef0a84d"
ARTIFACTS = ROOT / "_artifacts"

EVIDENCE: dict = {"checks": []}


def check(name: str, ok: bool, detail: str = "") -> None:
    EVIDENCE["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)[:600]})
    print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail else ""), flush=True)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_health(port: int, *, external_clients: int = 0) -> None:
    for _ in range(100):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    if data.get("external_clients", 0) >= external_clients:
                        return
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError("bridge did not become healthy")


async def recv_until(ws, predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError("no matching message")
        message = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
        if predicate(message):
            return message


def envelope(submission_id: str, endpoint_id: str, text: str) -> dict:
    return {
        "schema_version": 1,
        "submission_id": submission_id,
        "source": {
            "adapter": "chatgpt_web",
            "endpoint_id": endpoint_id,
            "native_session_id": SESSION,
            "message_id": "3207bbe8-7adc-429d-8dc0-f8d7fc8d89a0",
            "user_message_id": "5f94952d-1d49-466b-ba5f-1ca535589a19",
            "turn_group_id": "fallback-turn-16",
        },
        "content": {"format": "markdown", "text": text},
    }


async def main() -> int:
    temp_dir = tempfile.TemporaryDirectory()
    registry_path = Path(temp_dir.name) / "endpoints.json"
    store_path = Path(temp_dir.name) / "submissions.json"
    registry = EndpointRegistry(registry_path)
    binding, _ = registry.register("chatgpt_web", SESSION, "project")
    endpoint_id = binding.endpoint_id
    port = free_port()
    origin = f"http://127.0.0.1:{port}"

    env = dict(os.environ)
    env["CONTEXTFLOW_ENDPOINT_REGISTRY"] = str(registry_path)
    bridge = subprocess.Popen(
        [sys.executable, str(BRIDGE), "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
    )
    core = None
    page = None
    try:
        wait_health(port)
        core = subprocess.Popen(
            [sys.executable, str(MOCK_CORE), "--store", str(store_path),
             "--uri", f"ws://127.0.0.1:{port}/ws?token={TOKEN}", "--origin", origin],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        )
        wait_health(port, external_clients=1)
        page = await websockets.connect(f"ws://127.0.0.1:{port}/ws_page?token={TOKEN}", origin=origin)
        await recv_until(page, lambda m: m.get("type") == "welcome")

        # 1) endpoint lookup
        await page.send(json.dumps({"type": "endpoint_lookup", "request_id": "lookup-1",
                                    "session_id": SESSION}))
        lookup = await recv_until(page, lambda m: m.get("type") == "endpoint_lookup_result")
        check("endpoint lookup by native session", lookup.get("found") and lookup.get("endpoint_id") == endpoint_id,
              json.dumps(lookup, ensure_ascii=False))

        # 2) submit + accepted ack
        first = envelope("sub_" + uuid.uuid4().hex, endpoint_id, "first assistant body")
        await page.send(json.dumps({"type": "adapter_submission", "request_id": "req-1", "envelope": first}))
        ack1 = await recv_until(page, lambda m: m.get("type") == "submission_ack")
        check("submission accepted", ack1.get("status") == "accepted" and ack1.get("submission_id") == first["submission_id"],
              json.dumps(ack1, ensure_ascii=False))

        # 3) duplicate same envelope -> idempotent
        await page.send(json.dumps({"type": "adapter_submission", "request_id": "req-2", "envelope": first}))
        ack2 = await recv_until(page, lambda m: m.get("type") == "submission_ack")
        check("duplicate submission is idempotent",
              ack2.get("status") == "accepted" and ack2.get("duplicate") is True,
              json.dumps(ack2, ensure_ascii=False))

        # 4) same submission_id, different content -> conflict
        conflicting = envelope(first["submission_id"], endpoint_id, "different body")
        await page.send(json.dumps({"type": "adapter_submission", "request_id": "req-3", "envelope": conflicting}))
        ack3 = await recv_until(page, lambda m: m.get("type") == "submission_ack")
        check("conflicting content is rejected",
              ack3.get("status") == "conflict" and (ack3.get("error") or {}).get("code") == "SUBMISSION_CONFLICT",
              json.dumps(ack3, ensure_ascii=False))

        # 5) reverse send by Endpoint ID is routed to the page with the right session
        await page.send(json.dumps({"type": "bridge_result", "request_id": "noop",
                                    "result": {"id": "noop", "type": "result", "success": True}}))
        # The reverse-send request comes from the receiver process; we cannot
        # easily drive its stdin, so open a second external client here.
        external = await websockets.connect(f"ws://127.0.0.1:{port}/ws?token={TOKEN}", origin=origin)
        try:
            await recv_until(external, lambda m: m.get("type") == "welcome")
            await external.send(json.dumps({"id": "send-1", "type": "send_message_to_endpoint",
                                            "endpoint_id": endpoint_id, "text": "reverse test"}))
            request = await recv_until(page, lambda m: m.get("type") == "bridge_request")
            check("reverse send routed to the bound session",
                  request["request"].get("session_id") == SESSION
                  and request["request"].get("text") == "reverse test",
                  json.dumps(request["request"], ensure_ascii=False))
            await page.send(json.dumps({"type": "bridge_result", "request_id": "send-1",
                                        "result": {"id": "send-1", "type": "result", "success": True,
                                                   "data": {"sent": True, "verified": True}}}))
            result = await recv_until(external, lambda m: m.get("id") == "send-1")
            check("reverse send result returned", result.get("success") is True, json.dumps(result, ensure_ascii=False))
        finally:
            await external.close()

        # Inspect the receiver store on disk.
        receiver = MockCoreReceiver(store_path)
        check("receiver stored exactly one logical submission", receiver.count() == 1,
              f"count={receiver.count()}")
        stored = receiver.get(first["submission_id"])
        check("stored envelope keeps full body and identity",
              stored is not None
              and stored["envelope"]["content"]["text"] == "first assistant body"
              and stored["envelope"]["source"]["native_session_id"] == SESSION,
              json.dumps(stored["envelope"]["source"] if stored else None, ensure_ascii=False))

        passed = sum(1 for item in EVIDENCE["checks"] if item["ok"])
        EVIDENCE["summary"] = {"passed": passed, "total": len(EVIDENCE["checks"])}
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        (ARTIFACTS / "cfadapter001-protocol-evidence.json").write_text(
            json.dumps(EVIDENCE, ensure_ascii=False, indent=2), encoding="utf-8")
        if passed != len(EVIDENCE["checks"]) and core is not None:
            core.terminate()
            try:
                output = core.communicate(timeout=5)[0]
            except Exception:
                output = ""
            print("--- mock core output ---")
            print(output[-4000:])
        print(f"\n{passed}/{len(EVIDENCE['checks'])} checks passed")
        return 0 if passed == len(EVIDENCE["checks"]) else 1
    finally:
        if page is not None:
            await page.close()
        if core is not None:
            core.terminate()
        bridge.terminate()
        temp_dir.cleanup()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
