import asyncio
import json
import unittest

from websockets.asyncio.server import serve

from contextflow_core.adapter import WebSocketChatGPTAdapter
from contextflow_core.errors import ContextFlowError


class WebSocketAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = None
        self.clients = []

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def start_server(self, handler):
        self.server = await serve(handler, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        return f"ws://127.0.0.1:{port}/ws?token=test"

    def client(self, uri, timeout=1.0):
        client = WebSocketChatGPTAdapter(
            uri=uri,
            origin="http://127.0.0.1:8766",
            request_timeout=timeout,
        )
        self.clients.append(client)
        return client

    async def test_list_and_snapshot_use_unique_request_ids_and_exact_target(self):
        requests = []

        async def handler(connection):
            async for raw in connection:
                request = json.loads(raw)
                requests.append(request)
                if request["type"] == "list_sessions":
                    data = {"sessions": [{"session_id": "session-a", "tab_id": 17}]}
                else:
                    data = {"tab_id": 17, "snapshot": {"session": {"session_id": "session-a"}}}
                await connection.send(json.dumps({
                    "id": request["id"],
                    "type": "result",
                    "success": True,
                    "data": data,
                }))

        uri = await self.start_server(handler)
        client = self.client(uri)

        sessions = await client.list_sessions()
        snapshot = await client.get_snapshot("session-a", tab_id=17)

        self.assertEqual(sessions[0]["session_id"], "session-a")
        self.assertEqual(snapshot["tab_id"], 17)
        self.assertNotEqual(requests[0]["id"], requests[1]["id"])
        self.assertEqual(
            {key: requests[1][key] for key in ("type", "session_id", "tab_id")},
            {"type": "get_snapshot", "session_id": "session-a", "tab_id": 17},
        )

    async def test_bridge_error_envelope_becomes_structured_error(self):
        async def handler(connection):
            request = json.loads(await connection.recv())
            await connection.send(json.dumps({
                "id": request["id"],
                "type": "result",
                "success": False,
                "error": {"code": "AMBIGUOUS_SESSION", "message": "two tabs"},
            }))

        uri = await self.start_server(handler)
        client = self.client(uri)

        with self.assertRaises(ContextFlowError) as caught:
            await client.get_snapshot("session-a")

        self.assertEqual(caught.exception.code, "AMBIGUOUS_SESSION")
        self.assertEqual(caught.exception.message, "two tabs")

    async def test_client_timeout_is_explicit(self):
        async def handler(connection):
            await connection.recv()
            await asyncio.sleep(1)

        uri = await self.start_server(handler)
        client = self.client(uri, timeout=0.05)

        with self.assertRaises(ContextFlowError) as caught:
            await client.list_sessions()

        self.assertEqual(caught.exception.code, "BRIDGE_TIMEOUT")
        self.assertTrue(caught.exception.retryable)

    async def test_disconnect_fails_pending_request(self):
        async def handler(connection):
            await connection.recv()
            await connection.close()

        uri = await self.start_server(handler)
        client = self.client(uri)

        with self.assertRaises(ContextFlowError) as caught:
            await client.list_sessions()

        self.assertEqual(caught.exception.code, "BRIDGE_DISCONNECTED")

    async def test_later_call_reconnects_after_disconnect(self):
        connections = 0

        async def handler(connection):
            nonlocal connections
            connections += 1
            request = json.loads(await connection.recv())
            if connections == 1:
                await connection.close()
                return
            await connection.send(json.dumps({
                "id": request["id"],
                "type": "result",
                "success": True,
                "data": {"sessions": []},
            }))

        uri = await self.start_server(handler)
        client = self.client(uri)

        with self.assertRaises(ContextFlowError):
            await client.list_sessions()
        sessions = await client.list_sessions()

        self.assertEqual(sessions, [])
        self.assertEqual(connections, 2)


if __name__ == "__main__":
    unittest.main()
