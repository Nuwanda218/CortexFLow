import asyncio
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from contextflow_core import contracts as c
from contextflow_core.endpoint_identity import RegistryEndpointIdentityReader
from contextflow_core.message_store import SQLiteMessageStore
from contextflow_core.parse_jobs import JobState, SQLiteParseJobRepository
from contextflow_core.registry import EndpointRegistry
from contextflow_core.supervisor_control import SupervisorControlService
from tests.contracts.samples import supervisor_dict


class SupervisorControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.database = root / "messages.sqlite3"
        self.registry_path = root / "endpoints.json"
        self.registry = EndpointRegistry(self.registry_path)
        self.binding, _ = self.registry.register(
            "chatgpt_web", "native-session-1", "chat"
        )
        self.store = SQLiteMessageStore(
            self.database, RegistryEndpointIdentityReader(self.registry_path)
        )
        self.repository = SQLiteParseJobRepository(self.database)
        self.service = SupervisorControlService(
            self.registry, self.store, self.repository
        )

    async def asyncTearDown(self):
        await self.repository.close()
        await self.store.close()
        self.directory.cleanup()

    async def accept(self, suffix="1", *, binding=None, text="private supervisor body"):
        binding = binding or self.binding
        raw = supervisor_dict()
        raw["submission_id"] = f"sub_control_{suffix:0>8}"
        raw["source"].update(
            endpoint_id=binding.endpoint_id,
            native_session_id=binding.native_session_id,
            message_id=f"native-message-{suffix}",
        )
        raw["content"]["text"] = text
        result = await self.store.accept_message(c.SupervisorSubmission(envelope=raw))
        self.assertEqual(result.status, c.AcceptStatus.ACCEPTED)
        return raw, result.message_id

    def query(self, raw, **overrides):
        source = raw["source"]
        fields = {
            "submission_id": raw["submission_id"],
            "endpoint_id": source["endpoint_id"],
            "native_session_id": source["native_session_id"],
            "native_message_id": source["message_id"],
        }
        return {**fields, **overrides}

    async def test_constructor_rejects_missing_dependencies(self):
        with self.assertRaises(c.ContextFlowError):
            SupervisorControlService(object(), self.store, self.repository)
        with self.assertRaises(c.ContextFlowError):
            SupervisorControlService(self.registry, object(), self.repository)
        with self.assertRaises(c.ContextFlowError):
            SupervisorControlService(self.registry, self.store, object())

    async def test_bind_reloads_registry_and_preserves_external_records(self):
        external = EndpointRegistry(self.registry_path)
        external_b, created = external.register(
            "chatgpt_web", "external-session-b", "chat"
        )
        self.assertTrue(created)

        result = await self.service.handle("bind_supervisor", {
            "native_session_id": "service-session-c",
            "session_kind": "project",
        })

        self.assertTrue(result["success"])
        reloaded = EndpointRegistry(self.registry_path)
        self.assertIsNotNone(reloaded.get(self.binding.endpoint_id))
        self.assertIsNotNone(reloaded.get(external_b.endpoint_id))
        self.assertIsNotNone(reloaded.get(result["data"]["endpoint_id"]))
        self.assertEqual(len(reloaded.all()), 3)

    async def test_status_reload_sees_externally_added_binding(self):
        external = EndpointRegistry(self.registry_path)
        binding, _ = external.register(
            "chatgpt_web", "external-status-session", "chat"
        )
        # The MessageStore identity reader reloads the same Registry path, so this
        # Message is valid even though the service's construction snapshot predates it.
        raw, _ = await self.accept("external-status", binding=binding)

        result = await self.service.handle(
            "get_submission_status", self.query(raw)
        )

        self.assertTrue(result["success"])
        self.assertTrue(result["data"]["found"])
        self.assertEqual(result["data"]["core_status"], "accepted")

    async def test_cancelled_bind_waits_for_registry_effect_and_next_bind_preserves_it(self):
        original_register = EndpointRegistry.register
        started = threading.Event()
        release = threading.Event()

        def delayed_register(registry, provider, session_id, session_kind=None):
            if session_id == "cancelled-session-b":
                started.set()
                release.wait(timeout=2)
            return original_register(registry, provider, session_id, session_kind)

        with mock.patch.object(EndpointRegistry, "register", delayed_register):
            first = asyncio.create_task(self.service.bind_supervisor({
                "native_session_id": "cancelled-session-b",
                "session_kind": "chat",
            }))
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            first.cancel()
            await asyncio.sleep(0)
            self.assertFalse(first.done())

            second = asyncio.create_task(self.service.bind_supervisor({
                "native_session_id": "consecutive-session-c",
                "session_kind": "project",
            }))
            await asyncio.sleep(0.02)
            self.assertFalse(second.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await first
            second_result = await second

        registry = EndpointRegistry(self.registry_path)
        self.assertIsNotNone(
            registry.find("chatgpt_web", "cancelled-session-b")
        )
        self.assertIsNotNone(
            registry.get(second_result["endpoint_id"])
        )
        self.assertEqual(len(registry.all()), 3)

    async def test_bind_does_not_block_event_loop_while_registry_persists(self):
        original = EndpointRegistry.register
        started = threading.Event()
        release = threading.Event()

        def slow_register(registry, *args, **kwargs):
            started.set()
            release.wait(timeout=2)
            return original(registry, *args, **kwargs)

        with mock.patch.object(EndpointRegistry, "register", slow_register):
            task = asyncio.create_task(self.service.bind_supervisor({
                "native_session_id": "nonblocking-session",
                "session_kind": "chat",
            }))
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            before = time.monotonic()
            await asyncio.sleep(0)
            self.assertLess(time.monotonic() - before, 0.2)
            release.set()
            result = await task
        self.assertEqual(result["native_session_id"], "nonblocking-session")

    async def test_bind_is_core_generated_and_idempotent(self):
        params = {"native_session_id": "new-native-session", "session_kind": "project"}

        first = await self.service.handle("bind_supervisor", params)
        second = await self.service.handle("bind_supervisor", params)

        self.assertTrue(first["success"])
        self.assertEqual(set(first), {"success", "data"})
        self.assertEqual(first["data"]["endpoint_id"], second["data"]["endpoint_id"])
        self.assertRegex(first["data"]["endpoint_id"], r"^cfep_[0-9a-f]{32}$")
        self.assertEqual(first["data"], {
            "endpoint_id": first["data"]["endpoint_id"],
            "native_session_id": "new-native-session",
            "session_kind": "project",
            "created": True,
        })
        self.assertFalse(second["data"]["created"])

    async def test_bind_rejects_invalid_or_untrusted_fields(self):
        valid = {"native_session_id": "native-session", "session_kind": "chat"}
        cases = (
            None,
            {},
            {**valid, "endpoint_id": "cfep_" + "a" * 32},
            {**valid, "extra": True},
            {**valid, "native_session_id": ""},
            {**valid, "native_session_id": " outer"},
            {**valid, "native_session_id": "outer "},
            {**valid, "native_session_id": "x\nlog"},
            {**valid, "native_session_id": "x" * 257},
            {**valid, "session_kind": "draft"},
            {**valid, "session_kind": "new_chat"},
            {**valid, "session_kind": "CHAT"},
            {**valid, "session_kind": 1},
        )
        for params in cases:
            with self.subTest(params=params):
                result = await self.service.handle("bind_supervisor", params)
                self.assertEqual(result, {
                    "success": False,
                    "error": {
                        "code": "INVALID_CONTROL_REQUEST",
                        "message": "invalid Supervisor control request",
                    },
                })
        self.assertEqual(len(self.registry.all()), 1)

    async def test_ping_core_reports_fixed_availability_without_params(self):
        result = await self.service.handle("ping_core", {})
        self.assertEqual(result, {
            "success": True,
            "data": {"available": True},
        })

    async def test_ping_core_rejects_untrusted_params(self):
        for params in (None, [], {"extra": True}):
            with self.subTest(params=params):
                result = await self.service.handle("ping_core", params)
                self.assertEqual(result["error"]["code"], "INVALID_CONTROL_REQUEST")

    async def test_unknown_action_and_invalid_action_are_sanitized(self):
        for action in (None, "", " unknown", "unknown"):
            with self.subTest(action=action):
                result = await self.service.handle(action, {})
                self.assertFalse(result["success"])
                self.assertEqual(result["error"]["code"], "INVALID_CONTROL_REQUEST")
                self.assertEqual(set(result["error"]), {"code", "message"})

    async def test_status_requires_registry_and_message_ownership_without_enumeration(self):
        raw, _ = await self.accept("owner")
        other, _ = self.registry.register("chatgpt_web", "native-session-2", "chat")
        other_provider, _ = self.registry.register(
            "another_adapter", "native-session-1", "chat"
        )
        cases = (
            {**self.query(raw), "submission_id": "sub_" + "a" * 32},
            self.query(raw, endpoint_id=other.endpoint_id),
            self.query(raw, endpoint_id=other_provider.endpoint_id),
            self.query(raw, native_session_id="native-session-2"),
            self.query(raw, native_message_id="different-message"),
            {
                "submission_id": raw["submission_id"],
                "endpoint_id": other.endpoint_id,
                "native_session_id": other.native_session_id,
                "native_message_id": raw["source"]["message_id"],
            },
        )
        for params in cases:
            with self.subTest(params=params):
                result = await self.service.handle("get_submission_status", params)
                self.assertEqual(result, {"success": True, "data": {"found": False}})

    async def test_status_rejects_invalid_query_shape_before_lookup(self):
        raw, _ = await self.accept("shape")
        valid = self.query(raw)
        cases = (
            None,
            {},
            {**valid, "extra": True},
            {key: value for key, value in valid.items() if key != "native_message_id"},
            {**valid, "submission_id": "bad"},
            {**valid, "endpoint_id": "bad"},
            {**valid, "native_session_id": " "},
            {**valid, "native_message_id": "x\x00y"},
            {**valid, "native_message_id": 7},
        )
        for params in cases:
            with self.subTest(params=params):
                result = await self.service.handle("get_submission_status", params)
                self.assertFalse(result["success"])
                self.assertEqual(result["error"]["code"], "INVALID_CONTROL_REQUEST")

    async def test_no_job_and_ready_map_to_waiting(self):
        raw, message_id = await self.accept("waiting")
        expected_parser = {
            "status": "waiting",
            "instruction_count": 0,
            "target_agents": [],
            "error": None,
            "completed_at_ms": None,
        }
        before = await self.service.handle("get_submission_status", self.query(raw))
        self.assertEqual(before["data"]["parser"], expected_parser)

        await self.repository.discover_pending(limit=10, max_attempts=3)
        self.assertEqual((await self.repository.get_job(message_id)).state, JobState.READY)
        after = await self.service.handle("get_submission_status", self.query(raw))
        self.assertEqual(after["data"]["parser"], expected_parser)
        self.assertEqual(after["data"]["core_status"], "accepted")
        self.assertEqual(after["data"]["message_id"], str(message_id))
        self.assertNotIn("private supervisor body", json.dumps(after))

    async def test_processing_and_retry_wait_public_states(self):
        raw, message_id = await self.accept("process")
        await self.repository.discover_pending(limit=10, max_attempts=3)
        claim = await self.repository.claim_next(
            "control-worker", "control-claim", lease_duration_ms=5000
        )
        processing = await self.service.handle("get_submission_status", self.query(raw))
        self.assertEqual(processing["data"]["parser"], {
            "status": "processing",
            "instruction_count": 0,
            "target_agents": [],
            "error": None,
            "completed_at_ms": None,
        })

        await self.repository.begin_attempt(claim)
        error = c.ErrorInfo(
            code="TEMP_PROVIDER_PATH",
            message="retry without leaking D:/secret/database.sqlite3",
            retryable=True,
            details={"provider_body": "secret"},
        )
        await self.repository.schedule_retry(claim, error, delay_ms=60_000)
        self.assertEqual((await self.repository.get_job(message_id)).state, JobState.RETRY_WAIT)
        waiting = await self.service.handle("get_submission_status", self.query(raw))
        self.assertEqual(waiting["data"]["parser"], {
            "status": "retry_wait",
            "instruction_count": 0,
            "target_agents": [],
            "error": None,
            "completed_at_ms": None,
        })
        self.assertNotIn("database.sqlite3", json.dumps(waiting))
        self.assertNotIn("provider_body", json.dumps(waiting))

    async def test_completed_parsed_exposes_only_targets_and_count(self):
        raw, message_id = await self.accept("parsed", text="body must stay private")
        await self.repository.discover_pending(limit=10, max_attempts=3)
        claim = await self.repository.claim_next(
            "parsed-worker", "parsed-claim", lease_duration_ms=5000
        )
        await self.repository.begin_attempt(claim)
        parsed = c.ParseResult(
            status=c.ParseStatus.PARSED,
            instructions=(
                c.ParsedInstruction(target_agent="Codex", content="private fix body"),
                c.ParsedInstruction(target_agent="Claude Code", content="private test body"),
            ),
        )
        stored = await self.repository.finalize(claim, parsed)

        result = await self.service.handle("get_submission_status", self.query(raw))

        self.assertEqual(result["data"]["parser"], {
            "status": "parsed",
            "instruction_count": 2,
            "target_agents": ["Codex", "Claude Code"],
            "error": None,
            "completed_at_ms": stored.completed_at_ms,
        })
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("body must stay private", rendered)
        self.assertNotIn("private fix body", rendered)
        self.assertNotIn("lease", rendered)

    async def test_completed_unresolved_and_failed_have_capped_safe_errors(self):
        for index, status in enumerate((c.ParseStatus.UNRESOLVED, c.ParseStatus.FAILED)):
            with self.subTest(status=status):
                raw, _ = await self.accept(f"terminal-{index}")
                await self.repository.discover_pending(limit=10, max_attempts=3)
                claim = await self.repository.claim_next(
                    f"worker-{index}", f"claim-{index}", lease_duration_ms=5000
                )
                await self.repository.begin_attempt(claim)
                private_code = (
                    "INTERNAL_TARGET_ROUTER_PATH"
                    if status is c.ParseStatus.UNRESOLVED
                    else "PROVIDER_RAW_FAILURE"
                )
                private_message = (
                    "database D:/private/messages.sqlite3 returned provider body "
                    "with credential sk-private-not-real"
                )
                error = c.ErrorInfo(
                    code=private_code,
                    message=private_message,
                    retryable=status is c.ParseStatus.FAILED,
                    details={"path": "D:/secret.sqlite3", "provider": "private"},
                )
                stored = await self.repository.finalize(
                    claim, c.ParseResult(status=status, error=error)
                )

                result = await self.service.handle(
                    "get_submission_status", self.query(raw)
                )
                public = result["data"]["parser"]
                self.assertEqual(public["status"], status.value)
                self.assertEqual(public["instruction_count"], 0)
                self.assertEqual(public["target_agents"], [])
                self.assertEqual(public["completed_at_ms"], stored.completed_at_ms)
                self.assertEqual(public["error"], {
                    "code": "PARSER_UNRESOLVED" if status is c.ParseStatus.UNRESOLVED else "PARSER_FAILED",
                    "message": (
                        "Parser could not determine a complete, trusted instruction set"
                        if status is c.ParseStatus.UNRESOLVED
                        else "Parser could not complete instruction parsing"
                    ),
                    "retryable": status is c.ParseStatus.FAILED,
                })
                rendered = json.dumps(result)
                for secret in (
                    "secret.sqlite3", "messages.sqlite3", "provider",
                    "credential", "sk-private-not-real", private_code,
                ):
                    self.assertNotIn(secret, rendered)

    async def test_dependency_failures_are_sanitized_but_cancellation_propagates(self):
        class BrokenMessages:
            async def get_message_by_submission_id(self, submission_id):
                raise RuntimeError("private dependency body")

        broken = SupervisorControlService(self.registry, BrokenMessages(), self.repository)
        raw, _ = await self.accept("broken")
        result = await broken.handle("get_submission_status", self.query(raw))
        self.assertEqual(result, {
            "success": False,
            "error": {
                "code": "CORE_CONTROL_ERROR",
                "message": "Supervisor control operation failed",
            },
        })
        self.assertNotIn("private dependency body", json.dumps(result))

        class CancelledMessages:
            async def get_message_by_submission_id(self, submission_id):
                raise asyncio.CancelledError()

        cancelled = SupervisorControlService(
            self.registry, CancelledMessages(), self.repository
        )
        with self.assertRaises(asyncio.CancelledError):
            await cancelled.handle("get_submission_status", self.query(raw))


if __name__ == "__main__":
    unittest.main()
