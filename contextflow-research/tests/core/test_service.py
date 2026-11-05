import tempfile
import unittest
from pathlib import Path

from contextflow_core.errors import ContextFlowError
from contextflow_core.models import EndpointState
from contextflow_core.registry import EndpointRegistry
from contextflow_core.service import EndpointService
from tests.core.fakes import FakeAdapter


def session(
    session_id="session-a",
    tab_id=17,
    *,
    ready=True,
    kind="chat",
    url=None,
    error=None,
):
    return {
        "session_id": session_id,
        "tab_id": tab_id,
        "ready": ready,
        "kind": kind,
        "url": url or (f"https://chatgpt.com/c/{session_id}" if session_id else "https://chatgpt.com/"),
        "error": error,
    }


def snapshot(session_id="session-a", tab_id=17, *, messages=None):
    return {
        "tab_id": tab_id,
        "captured_at": "2026-11-05T01:02:03Z",
        "snapshot": {
            "session": {"session_id": session_id, "kind": "chat"},
            "page": {"url": f"https://chatgpt.com/c/{session_id}"},
            "generation": {"state": "completed"},
            "messages": list(messages or []),
        },
    }


class EndpointDiscoveryAndBindingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry = EndpointRegistry(Path(self.temp_dir.name) / "endpoints.json")

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    def service(self, adapter):
        return EndpointService(adapter=adapter, registry=self.registry)

    async def test_discover_normalizes_ready_and_draft_pages(self):
        adapter = FakeAdapter([
            session(),
            session(None, 18, ready=True, kind="new_chat"),
            session(None, 19, ready=False, kind=None,
                    error={"code": "CONTENT_SCRIPT_UNAVAILABLE", "message": "not injected"}),
        ])

        discovered = await self.service(adapter).discover()

        self.assertEqual(len(discovered), 3)
        self.assertTrue(discovered[0].bindable)
        self.assertFalse(discovered[1].bindable)
        self.assertFalse(discovered[2].bindable)
        self.assertEqual(discovered[2].error["code"], "CONTENT_SCRIPT_UNAVAILABLE")
        self.assertTrue(all(item.source == "open_tab" for item in discovered))

    async def test_bind_unique_session_creates_endpoint(self):
        service = self.service(FakeAdapter([session()]))

        result = await service.bind("session-a")

        self.assertTrue(result.created)
        self.assertEqual(result.endpoint.native_session_id, "session-a")
        self.assertNotEqual(result.endpoint.endpoint_id, "session-a")

    async def test_repeated_bind_returns_existing_endpoint(self):
        service = self.service(FakeAdapter([session()]))

        first = await service.bind("session-a")
        second = await service.bind("session-a")

        self.assertFalse(second.created)
        self.assertEqual(second.endpoint.endpoint_id, first.endpoint.endpoint_id)

    async def test_bind_missing_session_is_explicit(self):
        service = self.service(FakeAdapter([session("session-b", 18)]))

        with self.assertRaises(ContextFlowError) as caught:
            await service.bind("session-a")

        self.assertEqual(caught.exception.code, "SESSION_NOT_FOUND")

    async def test_bind_duplicate_session_is_explicit_and_creates_nothing(self):
        service = self.service(FakeAdapter([
            session("session-a", 17),
            session("session-a", 18),
        ]))

        with self.assertRaises(ContextFlowError) as caught:
            await service.bind("session-a")

        self.assertEqual(caught.exception.code, "AMBIGUOUS_SESSION")
        self.assertEqual(self.registry.all(), [])

    async def test_bind_never_substitutes_a_similar_session(self):
        service = self.service(FakeAdapter([session("session-a-copy", 18)]))

        with self.assertRaises(ContextFlowError) as caught:
            await service.bind("session-a")

        self.assertEqual(caught.exception.code, "SESSION_NOT_FOUND")

    async def test_bind_known_but_unready_session_fails(self):
        service = self.service(FakeAdapter([
            session("session-a", 17, ready=False,
                    error={"code": "CONTENT_SCRIPT_UNAVAILABLE", "message": "not injected"})
        ]))

        with self.assertRaises(ContextFlowError) as caught:
            await service.bind("session-a")

        self.assertEqual(caught.exception.code, "CONTENT_SCRIPT_UNAVAILABLE")


class EndpointResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry = EndpointRegistry(Path(self.temp_dir.name) / "endpoints.json")
        self.adapter = FakeAdapter([session()])
        self.service = EndpointService(adapter=self.adapter, registry=self.registry)
        self.binding = (await self.service.bind("session-a")).endpoint

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_resolve_returns_available_exact_location(self):
        resolution = await self.service.resolve(self.binding.endpoint_id)

        self.assertEqual(resolution.state, EndpointState.AVAILABLE)
        self.assertEqual(resolution.location.tab_id, 17)
        self.assertIsNone(resolution.reason_code)

    async def test_refresh_or_tab_replacement_keeps_endpoint_identity(self):
        self.adapter.sessions = [session("session-a", 42)]

        resolution = await self.service.resolve(self.binding.endpoint_id)

        self.assertEqual(resolution.endpoint_id, self.binding.endpoint_id)
        self.assertEqual(resolution.location.tab_id, 42)
        self.assertEqual(resolution.state, EndpointState.AVAILABLE)

    async def test_closed_tab_is_offline_without_deleting_binding(self):
        self.adapter.sessions = []

        resolution = await self.service.resolve(self.binding.endpoint_id)

        self.assertEqual(resolution.state, EndpointState.OFFLINE)
        self.assertEqual(resolution.reason_code, "SESSION_NOT_FOUND")
        self.assertEqual(self.registry.get(self.binding.endpoint_id), self.binding)

    async def test_duplicate_session_is_ambiguous_and_not_auto_selected(self):
        self.adapter.sessions = [session("session-a", 17), session("session-a", 18)]

        resolution = await self.service.resolve(self.binding.endpoint_id)

        self.assertEqual(resolution.state, EndpointState.AMBIGUOUS)
        self.assertEqual(resolution.reason_code, "AMBIGUOUS_SESSION")
        self.assertEqual([item.tab_id for item in resolution.candidates], [17, 18])
        self.assertIsNone(resolution.location)

    async def test_bridge_failure_is_offline_with_original_reason(self):
        self.adapter.list_script.append(
            ContextFlowError("BRIDGE_UNAVAILABLE", "bridge down", retryable=True)
        )

        resolution = await self.service.resolve(self.binding.endpoint_id)

        self.assertEqual(resolution.state, EndpointState.OFFLINE)
        self.assertEqual(resolution.reason_code, "BRIDGE_UNAVAILABLE")

    async def test_unready_last_tab_reports_content_script_failure(self):
        self.adapter.sessions = [
            session(None, 17, ready=False, kind=None,
                    error={"code": "CONTENT_SCRIPT_UNAVAILABLE", "message": "not injected"})
        ]

        resolution = await self.service.resolve(self.binding.endpoint_id)

        self.assertEqual(resolution.state, EndpointState.OFFLINE)
        self.assertEqual(resolution.reason_code, "CONTENT_SCRIPT_UNAVAILABLE")
        self.assertEqual(resolution.location.tab_id, 17)
        self.assertFalse(resolution.location.ready)

    async def test_connection_recovery_reuses_original_endpoint(self):
        self.adapter.list_script.append(
            ContextFlowError("BRIDGE_DISCONNECTED", "closed", retryable=True)
        )
        first = await self.service.resolve(self.binding.endpoint_id)
        second = await self.service.resolve(self.binding.endpoint_id)

        self.assertEqual(first.state, EndpointState.OFFLINE)
        self.assertEqual(second.state, EndpointState.AVAILABLE)
        self.assertEqual(second.endpoint_id, self.binding.endpoint_id)

    async def test_unknown_endpoint_is_not_treated_as_offline(self):
        with self.assertRaises(ContextFlowError) as caught:
            await self.service.resolve("cfep_missing")

        self.assertEqual(caught.exception.code, "ENDPOINT_NOT_FOUND")


class EndpointReadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry = EndpointRegistry(Path(self.temp_dir.name) / "endpoints.json")
        self.adapter = FakeAdapter(
            [session()],
            {("session-a", 17): snapshot(messages=[
                {
                    "message_id": "user-5",
                    "message_ids": ["user-5"],
                    "role": "user",
                    "content": "question",
                    "turn_key": "fallback-turn-5:0:user",
                    "turn_group_id": "fallback-turn-5",
                    "turn_position": 0,
                    "order": 8,
                    "status": "final",
                },
                {
                    "message_id": "assistant-5",
                    "message_ids": ["assistant-5"],
                    "role": "assistant",
                    "content": "answer",
                    "turn_key": "fallback-turn-5:2:assistant",
                    "turn_group_id": "fallback-turn-5",
                    "turn_position": 2,
                    "order": 9,
                    "status": "complete",
                },
            ])},
        )
        self.service = EndpointService(adapter=self.adapter, registry=self.registry)
        self.binding = (await self.service.bind("session-a")).endpoint

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_read_uses_exact_session_and_resolved_tab(self):
        result = await self.service.read(self.binding.endpoint_id)

        self.assertEqual(self.adapter.snapshot_calls, [("session-a", 17)])
        self.assertEqual(result.endpoint_id, self.binding.endpoint_id)
        self.assertEqual(result.native_session_id, "session-a")
        self.assertEqual(result.state, EndpointState.AVAILABLE)

    async def test_read_normalizes_turn_evidence_without_assuming_position_one(self):
        result = await self.service.read(self.binding.endpoint_id)

        self.assertEqual(len(result.messages), 2)
        self.assertEqual(result.messages[0].turn_group_id, "fallback-turn-5")
        self.assertEqual(result.messages[1].turn_group_id, "fallback-turn-5")
        self.assertEqual(result.messages[1].turn_position, 2)
        self.assertEqual(result.messages[1].evidence["turn_key"], "fallback-turn-5:2:assistant")
        self.assertEqual(result.history_scope, "rendered_dom")
        self.assertFalse(result.absence_means_deleted)

    async def test_offline_resolution_prevents_snapshot_read(self):
        self.adapter.sessions = []

        with self.assertRaises(ContextFlowError) as caught:
            await self.service.read(self.binding.endpoint_id)

        self.assertEqual(caught.exception.code, "SESSION_NOT_FOUND")
        self.assertEqual(self.adapter.snapshot_calls, [])
        self.assertEqual(caught.exception.details["resolution"]["state"], "offline")

    async def test_ambiguous_resolution_prevents_snapshot_read(self):
        self.adapter.sessions = [session("session-a", 17), session("session-a", 18)]

        with self.assertRaises(ContextFlowError) as caught:
            await self.service.read(self.binding.endpoint_id)

        self.assertEqual(caught.exception.code, "AMBIGUOUS_SESSION")
        self.assertEqual(self.adapter.snapshot_calls, [])

    async def test_session_changed_re_resolves_once_to_new_tab(self):
        self.adapter.list_script.extend([
            [session("session-a", 17)],
            [session("session-a", 42)],
        ])
        self.adapter.snapshot_script.extend([
            ContextFlowError("SESSION_CHANGED", "navigated"),
            snapshot(tab_id=42),
        ])

        result = await self.service.read(self.binding.endpoint_id)

        self.assertEqual(result.native_session_id, "session-a")
        self.assertEqual(self.adapter.snapshot_calls, [("session-a", 17), ("session-a", 42)])

    async def test_returned_wrong_session_is_rejected_then_retried_once(self):
        self.adapter.snapshot_script.extend([
            snapshot(session_id="session-b", tab_id=17),
            snapshot(session_id="session-a", tab_id=17),
        ])

        result = await self.service.read(self.binding.endpoint_id)

        self.assertEqual(result.native_session_id, "session-a")
        self.assertEqual(len(self.adapter.snapshot_calls), 2)

    async def test_persistent_read_failure_stops_after_one_retry(self):
        self.adapter.snapshot_script.extend([
            ContextFlowError("CONTENT_SCRIPT_UNAVAILABLE", "missing", retryable=True),
            ContextFlowError("CONTENT_SCRIPT_UNAVAILABLE", "still missing", retryable=True),
        ])

        with self.assertRaises(ContextFlowError) as caught:
            await self.service.read(self.binding.endpoint_id)

        self.assertEqual(caught.exception.code, "CONTENT_SCRIPT_UNAVAILABLE")
        self.assertEqual(len(self.adapter.snapshot_calls), 2)

    async def test_retry_stops_if_session_becomes_ambiguous(self):
        self.adapter.list_script.extend([
            [session("session-a", 17)],
            [session("session-a", 17), session("session-a", 18)],
        ])
        self.adapter.snapshot_script.append(ContextFlowError("SESSION_CHANGED", "navigated"))

        with self.assertRaises(ContextFlowError) as caught:
            await self.service.read(self.binding.endpoint_id)

        self.assertEqual(caught.exception.code, "AMBIGUOUS_SESSION")
        self.assertEqual(len(self.adapter.snapshot_calls), 1)


if __name__ == "__main__":
    unittest.main()
