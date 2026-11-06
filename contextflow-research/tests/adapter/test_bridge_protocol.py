"""CF-ADAPTER-001: bridge protocol integration tests (no browser).

These tests start the real ``bridge_server.py`` on an ephemeral port and use
two WebSocket clients:

* a fake *page* client (what bridge_client.html does in the browser), and
* a fake *external receiver* (what the Python Mock Core does).

They verify the transport contract that the browser Adapter relies on:
endpoint lookup, submission routing, acknowledgement routing, no-receiver
rejection, and reverse send by Endpoint ID.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[2]
BRIDGE_SCRIPT = ROOT / "contextflow-extension" / "bridge_server.py"
TOKEN = "dev-token-cf-exp-002"

from contextflow_core.registry import EndpointRegistry  # noqa: E402


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_envelope(submission_id: str, session_id: str, endpoint_id: str) -> dict:
    return {
        "schema_version": 1,
        "submission_id": submission_id,
        "source": {
            "adapter": "chatgpt_web",
            "endpoint_id": endpoint_id,
            "native_session_id": session_id,
            "message_id": "assistant-msg-1",
            "user_message_id": "user-msg-1",
            "turn_group_id": "fallback-turn-1",
        },
        "content": {"format": "markdown", "text": "hello from the adapter"},
    }


async def recv_until(ws, predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError("no matching message")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        message = json.loads(raw)
        if predicate(message):
            return message


class BridgeProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry_path = Path(self.temp_dir.name) / "endpoints.json"
        self.registry = EndpointRegistry(self.registry_path)
        self.binding, _ = self.registry.register(
            "chatgpt_web", "session-abc", "chat"
        )
        self.port = free_port()
        env = dict(os.environ)
        env["CONTEXTFLOW_ENDPOINT_REGISTRY"] = str(self.registry_path)
        self.proc = subprocess.Popen(
            [sys.executable, str(BRIDGE_SCRIPT), "--port", str(self.port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        self._wait_health()
        self.origin = f"http://127.0.0.1:{self.port}"
        self.page = await websockets.connect(
            f"ws://127.0.0.1:{self.port}/ws_page?token={TOKEN}", origin=self.origin
        )
        await recv_until(self.page, lambda m: m.get("type") == "welcome")
        self.core = await websockets.connect(
            f"ws://127.0.0.1:{self.port}/ws?token={TOKEN}", origin=self.origin
        )
        await recv_until(self.core, lambda m: m.get("type") == "welcome")

    async def asyncTearDown(self):
        try:
            await self.page.close()
        except Exception:
            pass
        try:
            await self.core.close()
        except Exception:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.temp_dir.cleanup()

    def _wait_health(self):
        url = f"http://127.0.0.1:{self.port}/health"
        for _ in range(80):
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    if response.status == 200:
                        return
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("bridge did not become healthy")

    async def test_endpoint_lookup_by_native_session(self):
        await self.page.send(json.dumps({
            "type": "endpoint_lookup", "request_id": "lookup-1", "session_id": "session-abc",
        }))
        result = await recv_until(self.page, lambda m: m.get("type") == "endpoint_lookup_result")
        self.assertEqual(result["request_id"], "lookup-1")
        self.assertTrue(result["found"])
        self.assertEqual(result["endpoint_id"], self.binding.endpoint_id)

    async def test_endpoint_lookup_unknown_session_reports_not_found(self):
        await self.page.send(json.dumps({
            "type": "endpoint_lookup", "request_id": "lookup-2", "session_id": "nope",
        }))
        result = await recv_until(self.page, lambda m: m.get("type") == "endpoint_lookup_result")
        self.assertFalse(result["found"])
        self.assertIsNone(result["endpoint_id"])

    async def test_submission_is_delivered_and_ack_routed_back(self):
        envelope = make_envelope("sub_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "session-abc",
                                 self.binding.endpoint_id)
        await self.page.send(json.dumps({
            "type": "adapter_submission", "request_id": "req-sub-1", "envelope": envelope,
        }))
        delivered = await recv_until(self.core, lambda m: m.get("type") == "submission")
        self.assertEqual(delivered["envelope"]["submission_id"], envelope["submission_id"])
        self.assertEqual(delivered["envelope"]["source"]["native_session_id"], "session-abc")

        await self.core.send(json.dumps({
            "type": "submission_ack",
            "submission_id": envelope["submission_id"],
            "status": "accepted",
            "duplicate": False,
        }))
        ack = await recv_until(self.page, lambda m: m.get("type") == "submission_ack")
        self.assertEqual(ack["request_id"], "req-sub-1")
        self.assertEqual(ack["status"], "accepted")
        self.assertEqual(ack["submission_id"], envelope["submission_id"])

    async def test_duplicate_ack_is_not_routed_twice(self):
        envelope = make_envelope("sub_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "session-abc",
                                 self.binding.endpoint_id)
        await self.page.send(json.dumps({
            "type": "adapter_submission", "request_id": "req-sub-2", "envelope": envelope,
        }))
        await recv_until(self.core, lambda m: m.get("type") == "submission")
        ack = {"type": "submission_ack", "submission_id": envelope["submission_id"],
               "status": "accepted", "duplicate": False}
        await self.core.send(json.dumps(ack))
        await recv_until(self.page, lambda m: m.get("type") == "submission_ack")
        await self.core.send(json.dumps(ack))
        with self.assertRaises(asyncio.TimeoutError):
            await recv_until(self.page, lambda m: m.get("type") == "submission_ack", timeout=0.6)

    async def test_submission_without_receiver_is_rejected(self):
        await self.core.close()
        await asyncio.sleep(0.2)
        envelope = make_envelope("sub_cccccccccccccccccccccccccccccccc", "session-abc",
                                 self.binding.endpoint_id)
        await self.page.send(json.dumps({
            "type": "adapter_submission", "request_id": "req-sub-3", "envelope": envelope,
        }))
        ack = await recv_until(self.page, lambda m: m.get("type") == "submission_ack")
        self.assertEqual(ack["status"], "rejected")
        self.assertEqual(ack["error"]["code"], "NO_RECEIVER")

    async def test_reverse_send_resolves_endpoint_and_relays_to_page(self):
        await self.core.send(json.dumps({
            "id": "send-1",
            "type": "send_message_to_endpoint",
            "endpoint_id": self.binding.endpoint_id,
            "text": "test message",
        }))
        request = await recv_until(self.page, lambda m: m.get("type") == "bridge_request")
        self.assertEqual(request["request"]["type"], "send_message_to_endpoint")
        self.assertEqual(request["request"]["session_id"], "session-abc")
        self.assertEqual(request["request"]["text"], "test message")

        await self.page.send(json.dumps({
            "type": "bridge_result",
            "request_id": "send-1",
            "result": {"id": "send-1", "type": "result", "success": True,
                       "data": {"sent": True, "verified": True}},
        }))
        result = await recv_until(self.core, lambda m: m.get("id") == "send-1")
        self.assertTrue(result["success"])
        self.assertTrue(result["data"]["verified"])

    async def test_reverse_send_unknown_endpoint_is_explicit(self):
        await self.core.send(json.dumps({
            "id": "send-2",
            "type": "send_message_to_endpoint",
            "endpoint_id": "cfep_doesnotexist",
            "text": "test message",
        }))
        result = await recv_until(self.core, lambda m: m.get("id") == "send-2")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "ENDPOINT_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
