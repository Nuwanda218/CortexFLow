"""C-01 Supervisor control correlation tests over the real local Bridge."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import time
import unittest
import urllib.request
from pathlib import Path

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

from tests.adapter.test_bridge_protocol import (
    BRIDGE_SCRIPT,
    BridgeTestCase,
    TOKEN,
    recv_until,
)


class SupervisorControlBridgeTests(BridgeTestCase):
    def uri(self, *, page: bool = False) -> str:
        endpoint = "ws_page" if page else "ws"
        return f"ws://127.0.0.1:{self.port}/{endpoint}?token={TOKEN}"

    async def wait_external_clients(self, expected: int) -> None:
        def read_count() -> int:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/health", timeout=1
            ) as response:
                return int(json.load(response)["external_clients"])

        for _ in range(100):
            if await asyncio.to_thread(read_count) == expected:
                return
            await asyncio.sleep(0.02)
        self.fail(f"external_clients did not become {expected}")

    async def send_control(
        self,
        request_id: object = "page-control-1",
        *,
        action: object = "bind_supervisor",
        params: object = None,
        extra: dict | None = None,
    ) -> None:
        if params is None:
            params = {
                "native_session_id": "session-abc",
                "session_kind": "chat",
            }
        frame = {
            "type": "supervisor_control_request",
            "request_id": request_id,
            "action": action,
            "params": params,
        }
        if extra:
            frame.update(extra)
        await self.page.send(json.dumps(frame))

    async def test_control_round_trip_rewrites_correlation_without_leaking_it(self):
        await self.send_control("page-visible-id")

        forwarded = await recv_until(
            self.core, lambda item: item.get("type") == "supervisor_control_request"
        )
        bridge_id = forwarded["request_id"]
        self.assertIsInstance(bridge_id, str)
        self.assertTrue(bridge_id.startswith("control-"))
        self.assertNotEqual(bridge_id, "page-visible-id")
        self.assertEqual(forwarded["action"], "bind_supervisor")
        self.assertEqual(forwarded["params"]["native_session_id"], "session-abc")
        self.assertEqual(
            set(forwarded), {"type", "request_id", "action", "params"}
        )

        await self.core.send(
            json.dumps(
                {
                    "type": "supervisor_control_result",
                    "request_id": bridge_id,
                    "success": True,
                    "data": {"endpoint_id": "cfep_" + "a" * 32},
                }
            )
        )
        result = await recv_until(
            self.page, lambda item: item.get("type") == "supervisor_control_result"
        )
        self.assertEqual(result["request_id"], "page-visible-id")
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["endpoint_id"], "cfep_" + "a" * 32)
        self.assertNotIn(bridge_id, json.dumps(result))

    async def test_control_without_core_returns_safe_failure_and_no_pending_record(self):
        await self.core.close()
        await self.wait_external_clients(0)

        await self.send_control("no-core")
        result = await recv_until(
            self.page, lambda item: item.get("type") == "supervisor_control_result"
        )

        self.assertEqual(
            result,
            {
                "type": "supervisor_control_result",
                "request_id": "no-core",
                "success": False,
                "error": {
                    "code": "CORE_UNAVAILABLE",
                    "message": "Core is not connected",
                },
            },
        )
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/health", timeout=1
        ) as response:
            self.assertEqual(json.load(response)["pending_controls"], 0)

    async def test_invalid_control_requests_are_sanitized_and_not_forwarded(self):
        cases = (
            ({
                "type": "supervisor_control_request",
                "action": "bind_supervisor",
                "params": {},
            }, None),
            ({
                "type": "supervisor_control_request",
                "request_id": {"untrusted": True},
                "action": "bind_supervisor",
                "params": {},
            }, None),
            ({
                "type": "supervisor_control_request",
                "request_id": "",
                "action": "bind_supervisor",
                "params": {},
            }, None),
            ({
                "type": "supervisor_control_request",
                "request_id": "bad-action",
                "action": "unknown",
                "params": {},
            }, "bad-action"),
            ({
                "type": "supervisor_control_request",
                "request_id": "bad-params",
                "action": "bind_supervisor",
                "params": [],
            }, "bad-params"),
            ({
                "type": "supervisor_control_request",
                "request_id": "extra-field",
                "action": "bind_supervisor",
                "params": {},
                "extra": "not allowed",
            }, "extra-field"),
            ({
                "type": "supervisor_control_request",
                "request_id": "bad-surrogate-\ud800",
                "action": "bind_supervisor",
                "params": {},
            }, None),
            ({
                "type": "supervisor_control_request",
                "request_id": "bad-param-unicode",
                "action": "bind_supervisor",
                "params": {"value": "\ud800"},
            }, "bad-param-unicode"),
        )
        for frame, correlation in cases:
            with self.subTest(frame=frame):
                await self.page.send(json.dumps(frame))
                result = await recv_until(
                    self.page,
                    lambda item: item.get("type")
                    == "supervisor_control_result",
                )
                self.assertEqual(result["request_id"], correlation)
                self.assertFalse(result["success"])
                self.assertEqual(
                    result["error"]["code"], "INVALID_CONTROL_REQUEST"
                )
                self.assertEqual(
                    set(result), {"type", "request_id", "success", "error"}
                )
        with self.assertRaises(asyncio.TimeoutError):
            await recv_until(
                self.core,
                lambda item: item.get("type") == "supervisor_control_request",
                timeout=0.3,
            )

    async def test_nonfinite_page_params_are_invalid_without_forwarding(self):
        for index, constant in enumerate((float("nan"), float("inf"), float("-inf"))):
            with self.subTest(constant=constant):
                request_id = f"nonfinite-page-{index}"
                await self.send_control(
                    request_id,
                    params={"value": constant},
                )
                result = await recv_until(
                    self.page,
                    lambda item: item.get("type")
                    == "supervisor_control_result",
                )
                self.assertEqual(result["request_id"], request_id)
                self.assertFalse(result["success"])
                self.assertEqual(
                    result["error"]["code"], "INVALID_CONTROL_REQUEST"
                )
        with self.assertRaises(asyncio.TimeoutError):
            await recv_until(
                self.core,
                lambda item: item.get("type") == "supervisor_control_request",
                timeout=0.3,
            )

    async def test_duplicate_page_request_is_not_forwarded_twice(self):
        await self.send_control("duplicate-control")
        first = await recv_until(
            self.core, lambda item: item.get("type") == "supervisor_control_request"
        )

        await self.send_control("duplicate-control")
        duplicate = await recv_until(
            self.page,
            lambda item: item.get("type") == "supervisor_control_result",
        )
        self.assertEqual(duplicate["request_id"], "duplicate-control")
        self.assertEqual(duplicate["error"]["code"], "CONTROL_INFLIGHT")

        await self.send_control(
            "duplicate-control",
            params={"native_session_id": "changed", "session_kind": "chat"},
        )
        conflict = await recv_until(
            self.page,
            lambda item: item.get("type") == "supervisor_control_result",
        )
        self.assertEqual(
            conflict["error"]["code"], "CONTROL_INFLIGHT_CONFLICT"
        )

        with self.assertRaises(asyncio.TimeoutError):
            await recv_until(
                self.core,
                lambda item: item.get("type") == "supervisor_control_request",
                timeout=0.3,
            )

        await self.core.send(
            json.dumps(
                {
                    "type": "supervisor_control_result",
                    "request_id": first["request_id"],
                    "success": True,
                    "data": {"ok": True},
                }
            )
        )
        final = await recv_until(
            self.page,
            lambda item: item.get("type") == "supervisor_control_result",
        )
        self.assertTrue(final["success"])

    async def test_wrong_and_malformed_core_results_do_not_pass_through(self):
        await self.send_control("core-result")
        forwarded = await recv_until(
            self.core, lambda item: item.get("type") == "supervisor_control_request"
        )

        await self.core.send(
            json.dumps(
                {
                    "type": "supervisor_control_result",
                    "request_id": "control-wrong",
                    "success": True,
                    "data": {"private": "must not route"},
                }
            )
        )
        with self.assertRaises(asyncio.TimeoutError):
            await recv_until(
                self.page,
                lambda item: item.get("type") == "supervisor_control_result",
                timeout=0.3,
            )

        await self.core.send(
            json.dumps(
                {
                    "type": "supervisor_control_result",
                    "request_id": forwarded["request_id"],
                    "success": "not-a-boolean",
                    "data": {"private": "must not route"},
                }
            )
        )
        result = await recv_until(
            self.page,
            lambda item: item.get("type") == "supervisor_control_result",
        )
        self.assertEqual(result["request_id"], "core-result")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "CORE_PROTOCOL_ERROR")
        self.assertNotIn("private", json.dumps(result))

    async def test_nonfinite_core_data_is_a_protocol_error(self):
        for index, constant in enumerate((float("nan"), float("inf"), float("-inf"))):
            with self.subTest(constant=constant):
                page_id = f"nonfinite-core-{index}"
                await self.send_control(page_id)
                forwarded = await recv_until(
                    self.core,
                    lambda item: item.get("type")
                    == "supervisor_control_request",
                )
                await self.core.send(
                    json.dumps(
                        {
                            "type": "supervisor_control_result",
                            "request_id": forwarded["request_id"],
                            "success": True,
                            "data": {"value": constant},
                        }
                    )
                )
                result = await recv_until(
                    self.page,
                    lambda item: item.get("type")
                    == "supervisor_control_result",
                )
                self.assertEqual(result["request_id"], page_id)
                self.assertFalse(result["success"])
                self.assertEqual(
                    result["error"]["code"], "CORE_PROTOCOL_ERROR"
                )

    async def test_invalid_unicode_from_core_is_a_protocol_error(self):
        cases = (
            {"success": True, "data": {"value": "\ud800"}},
            {
                "success": False,
                "error": {"code": "BAD_\ud800", "message": "message"},
            },
            {
                "success": False,
                "error": {"code": "BAD", "message": "message \ud800"},
            },
        )
        for index, payload in enumerate(cases):
            with self.subTest(payload=payload):
                page_id = f"unicode-core-{index}"
                await self.send_control(page_id)
                forwarded = await recv_until(
                    self.core,
                    lambda item: item.get("type")
                    == "supervisor_control_request",
                )
                await self.core.send(
                    json.dumps(
                        {
                            "type": "supervisor_control_result",
                            "request_id": forwarded["request_id"],
                            **payload,
                        }
                    )
                )
                result = await recv_until(
                    self.page,
                    lambda item: item.get("type")
                    == "supervisor_control_result",
                )
                self.assertEqual(result["request_id"], page_id)
                self.assertFalse(result["success"])
                self.assertEqual(
                    result["error"]["code"], "CORE_PROTOCOL_ERROR"
                )

    async def test_late_result_from_replacement_core_is_ignored(self):
        await self.send_control("old-core")
        forwarded = await recv_until(
            self.core, lambda item: item.get("type") == "supervisor_control_request"
        )
        await self.core.close()
        await self.wait_external_clients(0)
        self.core = await websockets.connect(self.uri(), origin=self.origin)
        await recv_until(self.core, lambda item: item.get("type") == "welcome")

        await self.core.send(
            json.dumps(
                {
                    "type": "supervisor_control_result",
                    "request_id": forwarded["request_id"],
                    "success": True,
                    "data": {"late": True},
                }
            )
        )
        with self.assertRaises(asyncio.TimeoutError):
            await recv_until(
                self.page,
                lambda item: item.get("type") == "supervisor_control_result",
                timeout=0.3,
            )

        await self.send_control("new-core")
        fresh = await recv_until(
            self.core, lambda item: item.get("type") == "supervisor_control_request"
        )
        self.assertNotEqual(fresh["request_id"], forwarded["request_id"])

    async def test_late_result_is_not_routed_to_replacement_page(self):
        await self.send_control("old-page")
        forwarded = await recv_until(
            self.core, lambda item: item.get("type") == "supervisor_control_request"
        )
        await self.page.close()
        self.page = await websockets.connect(self.uri(page=True), origin=self.origin)
        await recv_until(self.page, lambda item: item.get("type") == "welcome")

        await self.core.send(
            json.dumps(
                {
                    "type": "supervisor_control_result",
                    "request_id": forwarded["request_id"],
                    "success": True,
                    "data": {"late": True},
                }
            )
        )
        with self.assertRaises(asyncio.TimeoutError):
            await recv_until(
                self.page,
                lambda item: item.get("type") == "supervisor_control_result",
                timeout=0.3,
            )

        await self.send_control("new-page")
        fresh = await recv_until(
            self.core, lambda item: item.get("type") == "supervisor_control_request"
        )
        self.assertNotEqual(fresh["request_id"], forwarded["request_id"])

    async def test_disallowed_and_prefix_spoofed_origins_are_rejected(self):
        for origin in (
            "https://evil.example",
            "http://127.0.0.1.evil.example",
            "chrome-extension.evil://extension-id",
        ):
            with self.subTest(origin=origin):
                with self.assertRaises(InvalidStatus) as caught:
                    await websockets.connect(self.uri(page=True), origin=origin)
                self.assertEqual(caught.exception.response.status_code, 403)

    async def test_expired_control_result_is_ignored_without_another_request(self):
        env = dict(os.environ)
        env["CF_BRIDGE_CONTROL_TTL_S"] = "0.15"
        # Restart only this test's real Bridge with a short lifecycle TTL.
        await self.page.close()
        await self.core.close()
        self.proc.terminate()
        self.proc.wait(timeout=5)
        self.proc = __import__("subprocess").Popen(
            [__import__("sys").executable, str(BRIDGE_SCRIPT), "--port", str(self.port)],
            stdout=__import__("subprocess").DEVNULL,
            stderr=__import__("subprocess").DEVNULL,
            env=env,
        )
        self._wait_health()
        self.page = await websockets.connect(self.uri(page=True), origin=self.origin)
        await recv_until(self.page, lambda item: item.get("type") == "welcome")
        self.core = await websockets.connect(self.uri(), origin=self.origin)
        await recv_until(self.core, lambda item: item.get("type") == "welcome")

        await self.send_control("expires-without-traffic")
        forwarded = await recv_until(
            self.core,
            lambda item: item.get("type") == "supervisor_control_request",
        )
        await asyncio.sleep(0.35)
        await self.core.send(
            json.dumps(
                {
                    "type": "supervisor_control_result",
                    "request_id": forwarded["request_id"],
                    "success": True,
                    "data": {"late": True},
                }
            )
        )
        with self.assertRaises(asyncio.TimeoutError):
            await recv_until(
                self.page,
                lambda item: item.get("type") == "supervisor_control_result",
                timeout=0.3,
            )
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/health", timeout=1
        ) as response:
            self.assertEqual(json.load(response)["pending_controls"], 0)

    async def test_health_exposes_only_operational_counts(self):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/health", timeout=1
        ) as response:
            health = json.load(response)
        self.assertEqual(
            set(health),
            {
                "ok",
                "page_connected",
                "external_clients",
                "pending_requests",
                "pending_submissions",
                "pending_controls",
            },
        )
        self.assertNotIn("registry_path", health)
        self.assertNotIn("clients", health)
        rendered = json.dumps(health)
        for forbidden in (
            str(self.registry_path),
            TOKEN,
            "session-abc",
        ):
            self.assertNotIn(forbidden, rendered)


class SupervisorControlBridgeUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("c01_bridge_server", BRIDGE_SCRIPT)
        cls.module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(cls.module)

    def setUp(self):
        self.previous = dict(self.module.state["pending_controls"])
        self.module.state["pending_controls"].clear()

    def tearDown(self):
        self.module.state["pending_controls"].clear()
        self.module.state["pending_controls"].update(self.previous)

    def test_expired_pending_controls_are_removed_without_waiting(self):
        self.module.state["pending_controls"]["control-stale"] = {
            "page_request_id": "page-stale",
            "page_client_id": "page-old",
            "receiver_client_id": "core-old",
            "canonical_request": "{}",
            "at": time.time() - self.module.PENDING_CONTROL_TTL_S - 1,
        }
        self.module.state["pending_controls"]["control-live"] = {
            "page_request_id": "page-live",
            "page_client_id": "page-current",
            "receiver_client_id": "core-current",
            "canonical_request": "{}",
            "at": time.time(),
        }

        self.module.cleanup_pending_controls()

        self.assertNotIn("control-stale", self.module.state["pending_controls"])
        self.assertIn("control-live", self.module.state["pending_controls"])

    def test_forwarded_control_frame_size_includes_bridge_correlation(self):
        module = self.module
        original_factory = module._new_control_id
        module._new_control_id = lambda: "control-" + "a" * 32
        try:
            params = {"padding": ""}
            while module._serialized_control_frame(
                "bind_supervisor", params
            )[1] < module.MAX_CONTROL_FRAME_BYTES:
                remaining = (
                    module.MAX_CONTROL_FRAME_BYTES
                    - module._serialized_control_frame("bind_supervisor", params)[1]
                )
                params["padding"] += "x" * remaining
            _, exact_size = module._serialized_control_frame(
                "bind_supervisor", params
            )
            self.assertEqual(exact_size, module.MAX_CONTROL_FRAME_BYTES)
            self.assertTrue(module._control_frame_fits("bind_supervisor", params))
            params["padding"] += "x"
            self.assertFalse(module._control_frame_fits("bind_supervisor", params))
        finally:
            module._new_control_id = original_factory

    def test_origin_allowlist_requires_exact_local_or_extension_origin(self):
        module = self.module
        self.assertTrue(module.origin_allowed(None))
        self.assertTrue(module.origin_allowed(""))
        self.assertTrue(module.origin_allowed("http://127.0.0.1"))
        self.assertTrue(module.origin_allowed("http://localhost/"))
        self.assertTrue(module.origin_allowed("http://127.0.0.1:8766"))
        self.assertTrue(module.origin_allowed("http://localhost:8766/"))
        self.assertTrue(module.origin_allowed("chrome-extension://abcdefghijklmnop"))
        for invalid in (
            "http://localhost?",
            "http://localhost#",
            "chrome-extension://abcdefghijklmnop?",
            "chrome-extension://abcdefghijklmnop#",
            "http://localhost:",
            "http://127.0.0.1:",
            "chrome-extension://abcdefghijklmnop:",
            "http://127.0.0.1:bad",
            "http://127.0.0.1:8766/path",
            "http://localhost:8766/path",
            "http://user@127.0.0.1:8766",
            "http://127.0.0.1:8766?query=1",
            "chrome-extension://abcdefghijklmnop:123",
            "chrome-extension://abcdefghijklmnop/path",
        ):
            with self.subTest(origin=invalid):
                self.assertFalse(module.origin_allowed(invalid))

    def test_dedicated_and_fallback_relays_have_control_event_parity(self):
        extension = BRIDGE_SCRIPT.parent
        dedicated = (extension / "bridge_relay.js").read_text(encoding="utf-8")
        fallback = (extension / "content.js").read_text(encoding="utf-8")
        for source in (dedicated, fallback):
            self.assertIn('msg.type === "cf:bridge_control"', source)
            self.assertIn('request_event: "cf:supervisor_control_request"', source)
            self.assertIn('result_event: "cf:supervisor_control_result"', source)
            self.assertIn("payload: { action: msg.action, params: msg.params }", source)


if __name__ == "__main__":
    unittest.main()
