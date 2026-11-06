"""Port conformance using canned snapshots, never business implementations."""

from __future__ import annotations

import inspect
from pathlib import Path
import subprocess
import sys
from typing import get_type_hints
import unittest

from contextflow_core import contracts as c
from contextflow_core.errors import ContextFlowError
from tests.contracts.samples import (
    AGENT_ID,
    ENDPOINT_ID,
    MESSAGE_ID,
    REPORT_MESSAGE_ID,
    TASK_ID,
    instruction,
    parsed,
    report_delivery,
    report_dict,
    report_message,
    supervisor_dict,
    supervisor_message,
    task,
    task_delivery,
)


class FakeMessageStore:
    def __init__(self):
        self.calls = []

    async def accept_message(self, submission: c.MessageSubmission) -> c.AcceptResult:
        self.calls.append(("accept", submission))
        return c.AcceptResult(status=c.AcceptStatus.ACCEPTED, message_id=MESSAGE_ID)

    async def get_message(self, message_id: c.MessageId) -> c.Message | None:
        self.calls.append(("get", message_id))
        return supervisor_message()

    async def list_pending(self, kind: c.MessageKind, limit: int) -> tuple[c.Message, ...]:
        self.calls.append(("pending", kind, limit))
        return (supervisor_message(),)

    async def mark_processed(self, message_id: c.MessageId) -> None:
        self.calls.append(("processed", message_id))

    async def mark_blocked(self, message_id: c.MessageId, error: c.ErrorInfo) -> None:
        self.calls.append(("blocked", message_id, error))


class FakeLlm:
    async def extract(self, text: str, known_agents: tuple[str, ...] | None = None) -> object:
        return {"instructions": [{"target_agent": "codex", "content": "修复 Adapter"}]}


class FakeAgentDirectory:
    async def known_agents(self) -> tuple[str, ...]:
        return ("codex",)

    async def resolve(self, target: str) -> c.AgentDescriptor | None:
        return c.AgentDescriptor(agent_id=AGENT_ID, available=False)

    async def get_agent(self, agent_id: c.AgentId) -> c.AgentDescriptor | None:
        return c.AgentDescriptor(agent_id=AGENT_ID, available=False)


class FakeAgentGateway:
    def __init__(self):
        self.calls = []

    async def send_task(self, agent_id: c.AgentId, task_id: c.TaskId, content: str) -> c.DeliveryOutcome:
        self.calls.append((agent_id, task_id, content))
        return c.DeliveryOutcome(
            state=c.DeliveryState.CONFIRMED,
            evidence={"task_id": str(task_id), "status": "accepted"},
        )


class FakeTaskAccess:
    async def get_task(self, task_id: c.TaskId) -> c.Task | None:
        return task()

    async def get_tasks_for_message(self, message_id: c.MessageId) -> tuple[c.Task, ...]:
        return (task(),)

    async def finish_task(
        self,
        task_id: c.TaskId,
        final_report_message_id: c.MessageId,
        outcome: c.ReportOutcome,
    ) -> c.Task:
        # Canned response only: final-report uniqueness belongs to phase C/D.
        return task(state=c.TaskState.COMPLETED, final_report_message_id=REPORT_MESSAGE_ID)


class FakeEndpointReader:
    async def get_identity(self, endpoint_id: c.EndpointId) -> c.EndpointIdentity | None:
        return c.EndpointIdentity(
            endpoint_id=ENDPOINT_ID,
            provider="chatgpt_web",
            native_session_id=c.NativeSessionId("native-session-1"),
        )


class FakeSupervisorGateway:
    def __init__(self):
        self.calls = []

    async def connect(self) -> None:
        self.calls.append("connect")

    async def close(self) -> None:
        self.calls.append("close")

    async def submissions(self):
        yield c.SupervisorSubmission(envelope=supervisor_dict())

    async def acknowledge(self, submission_id: str | None, result: c.AcceptResult) -> None:
        self.calls.append(("ack", submission_id, result))

    async def send_message_to_endpoint(self, endpoint_id: c.EndpointId, text: str) -> c.DeliveryOutcome:
        self.calls.append(("send", endpoint_id, text))
        return c.DeliveryOutcome(state=c.DeliveryState.CONFIRMED, evidence={"verified": True})


class FakeParser:
    async def parse(self, text: str, known_agents: tuple[str, ...] | None = None) -> c.ParseResult:
        return parsed()


class FakeDispatcher:
    async def create_tasks(self, source_message_id: c.MessageId, parsed: c.ParseResult) -> c.CreateTasksResult:
        return c.CreateTasksResult(
            status=c.CreateTasksStatus.CREATED,
            tasks=(task(),),
            deliveries=(task_delivery(),),
            intent_fingerprint=parsed.intent_fingerprint,
        )

    async def dispatch_pending(self, source_message_id: c.MessageId) -> tuple[c.Delivery, ...]:
        return (task_delivery(),)

    async def list_pending_batches(self, limit: int) -> tuple[c.MessageId, ...]:
        return (MESSAGE_ID,)

    async def recover_inflight(self) -> tuple[c.Delivery, ...]:
        return ()


class FakeReportReturn:
    async def accept_report(self, source_agent_id: c.AgentId, envelope: object) -> c.AcceptResult:
        return c.AcceptResult(status=c.AcceptStatus.ACCEPTED, message_id=REPORT_MESSAGE_ID)

    async def process_report(self, message_id: c.MessageId) -> c.ProcessResult:
        return c.ProcessResult(message_id=REPORT_MESSAGE_ID, state=c.ProcessingState.PROCESSED)

    async def deliver_pending(self, limit: int) -> tuple[c.Delivery, ...]:
        return (report_delivery(),)

    async def recover_inflight(self) -> tuple[c.Delivery, ...]:
        return ()


class FakeMessageProcessor:
    async def accept_supervisor(self, envelope: object) -> c.AcceptResult:
        return c.AcceptResult(status=c.AcceptStatus.ACCEPTED, message_id=MESSAGE_ID)

    async def process_message(self, message_id: c.MessageId) -> c.ProcessResult:
        return c.ProcessResult(message_id=message_id, state=c.ProcessingState.PROCESSED)

    async def recover_once(self, limit: int) -> None:
        return None


class PortValueTests(unittest.TestCase):
    def test_descriptor_validates_stable_agent_identity_and_availability(self):
        offline = c.AgentDescriptor(agent_id=AGENT_ID, available=False)
        self.assertFalse(offline.available)
        for fields in ({"agent_id": ENDPOINT_ID, "available": True}, {"agent_id": AGENT_ID, "available": 1}):
            with self.subTest(fields=fields):
                with self.assertRaises(ContextFlowError):
                    c.AgentDescriptor(**fields)

    def test_endpoint_identity_does_not_reuse_a_browser_location_as_identity(self):
        identity = c.EndpointIdentity(
            endpoint_id=ENDPOINT_ID,
            provider="chatgpt_web",
            native_session_id=c.NativeSessionId("session-1"),
        )
        self.assertFalse(hasattr(identity, "tab_id"))
        with self.assertRaises(ContextFlowError):
            c.EndpointIdentity(endpoint_id=AGENT_ID, provider="chatgpt_web", native_session_id=c.NativeSessionId("session-1"))
        with self.assertRaises(ContextFlowError):
            c.EndpointIdentity(endpoint_id=ENDPOINT_ID, provider="", native_session_id=c.NativeSessionId("session-1"))

    def test_store_input_distinguishes_unvalidated_supervisor_and_trusted_agent_source(self):
        raw = c.SupervisorSubmission(envelope={"invalid": True})
        self.assertEqual(raw.envelope, {"invalid": True})
        submitted = c.AgentSubmission(source_agent_id=AGENT_ID, envelope=c.AgentReportEnvelope.from_dict(report_dict()))
        self.assertEqual(submitted.source_agent_id, AGENT_ID)
        with self.assertRaises(ContextFlowError):
            c.AgentSubmission(source_agent_id=ENDPOINT_ID, envelope=submitted.envelope)
        with self.assertRaises(ContextFlowError):
            c.AgentSubmission(source_agent_id=AGENT_ID, envelope=report_dict())

    def test_public_port_annotations_resolve_without_future_business_modules(self):
        names = (
            "MessageReader", "MessageStore", "LlmExtractionPort", "AgentDirectory",
            "AgentGateway", "TaskQuery", "TaskStatus", "EndpointIdentityReader",
            "SupervisorGateway", "InstructionParser", "TaskDispatcher", "ReportReturn",
            "MessageProcessor",
        )
        for name in names:
            with self.subTest(name=name):
                protocol = getattr(c, name)
                self.assertTrue(protocol._is_protocol)
                for method_name, method in inspect.getmembers(protocol, inspect.isfunction):
                    if not method_name.startswith("_"):
                        self.assertTrue(get_type_hints(method))
        modules = (
            "contextflow_core.message_store", "contextflow_core.instruction_parser",
            "contextflow_core.dispatch", "contextflow_core.report_return", "contextflow_core.runtime",
        )
        script = (
            "import sys\nfrom contextflow_core import contracts\n"
            f"loaded = set({modules!r}).intersection(sys.modules)\n"
            "assert not loaded, sorted(loaded)\n"
        )
        result = subprocess.run(
            [sys.executable, "-B", "-c", script],
            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class PortFakeTests(unittest.IsolatedAsyncioTestCase):
    async def test_message_store_fake_accepts_queries_and_processing_marks(self):
        store: c.MessageStore = FakeMessageStore()
        self.assertIsInstance(store, c.MessageStore)
        self.assertIsInstance(store, c.MessageReader)
        submission = c.SupervisorSubmission(envelope=supervisor_dict())
        accepted = await store.accept_message(submission)
        message = await store.get_message(accepted.message_id)
        pending = await store.list_pending(c.MessageKind.SUPERVISOR_INSTRUCTION, 1)
        await store.mark_processed(MESSAGE_ID)
        await store.mark_blocked(MESSAGE_ID, c.ErrorInfo(code="TARGET_UNRESOLVED", message="No agent"))
        self.assertEqual(message.message_id, pending[0].message_id)
        self.assertEqual(store.calls[0], ("accept", submission))
        self.assertEqual(len(store.calls), 5)

    async def test_llm_and_parser_fakes_are_separate_ports(self):
        llm: c.LlmExtractionPort = FakeLlm()
        parser: c.InstructionParser = FakeParser()
        self.assertIsInstance(llm, c.LlmExtractionPort)
        self.assertIsInstance(parser, c.InstructionParser)
        raw = await llm.extract("Supervisor instruction", ("codex",))
        result = await parser.parse("Supervisor instruction", ("codex",))
        self.assertEqual(raw["instructions"][0]["target_agent"], "codex")
        self.assertEqual(result.status, c.ParseStatus.PARSED)

    async def test_agent_directory_keeps_known_offline_agents_in_its_catalog(self):
        directory: c.AgentDirectory = FakeAgentDirectory()
        self.assertIsInstance(directory, c.AgentDirectory)
        names = await directory.known_agents()
        target = await directory.resolve(names[0])
        by_id = await directory.get_agent(target.agent_id)
        self.assertEqual(target.agent_id, by_id.agent_id)
        self.assertFalse(target.available)

    async def test_agent_gateway_transports_task_id_without_claiming_completion(self):
        gateway: c.AgentGateway = FakeAgentGateway()
        self.assertIsInstance(gateway, c.AgentGateway)
        outcome = await gateway.send_task(AGENT_ID, TASK_ID, instruction().content)
        self.assertEqual(gateway.calls[0][1], TASK_ID)
        self.assertEqual(outcome.state, c.DeliveryState.CONFIRMED)
        self.assertNotEqual(task().state, c.TaskState.COMPLETED)

    async def test_task_query_and_status_are_independent_of_dispatcher_internals(self):
        access = FakeTaskAccess()
        query: c.TaskQuery = access
        status: c.TaskStatus = access
        self.assertIsInstance(query, c.TaskQuery)
        self.assertIsInstance(status, c.TaskStatus)
        existing = await query.get_tasks_for_message(MESSAGE_ID)
        one = await query.get_task(TASK_ID)
        final = await status.finish_task(TASK_ID, REPORT_MESSAGE_ID, c.ReportOutcome.COMPLETED)
        self.assertEqual(existing[0].task_id, one.task_id)
        self.assertEqual(final.final_report_message_id, REPORT_MESSAGE_ID)

    async def test_endpoint_reader_needs_no_registry_instance(self):
        reader: c.EndpointIdentityReader = FakeEndpointReader()
        self.assertIsInstance(reader, c.EndpointIdentityReader)
        identity = await reader.get_identity(ENDPOINT_ID)
        self.assertEqual(identity.endpoint_id, ENDPOINT_ID)
        self.assertEqual(identity.provider, "chatgpt_web")

    async def test_single_supervisor_gateway_exposes_both_directions(self):
        gateway: c.SupervisorGateway = FakeSupervisorGateway()
        self.assertIsInstance(gateway, c.SupervisorGateway)
        await gateway.connect()
        submissions = [event async for event in gateway.submissions()]
        result = c.AcceptResult(status=c.AcceptStatus.ACCEPTED, message_id=MESSAGE_ID)
        await gateway.acknowledge(submissions[0].envelope["submission_id"], result)
        sent = await gateway.send_message_to_endpoint(ENDPOINT_ID, "Report")
        await gateway.close()
        self.assertEqual(sent.state, c.DeliveryState.CONFIRMED)
        self.assertEqual(gateway.calls[0], "connect")
        self.assertEqual(gateway.calls[-1], "close")

    async def test_dispatcher_port_exposes_commit_and_recovery_without_implementation(self):
        dispatcher: c.TaskDispatcher = FakeDispatcher()
        self.assertIsInstance(dispatcher, c.TaskDispatcher)
        committed = await dispatcher.create_tasks(MESSAGE_ID, parsed())
        deliveries = await dispatcher.dispatch_pending(MESSAGE_ID)
        batches = await dispatcher.list_pending_batches(1)
        recovered = await dispatcher.recover_inflight()
        self.assertTrue(committed.is_committed)
        self.assertEqual(deliveries[0].subject_id, TASK_ID)
        self.assertEqual(batches, (MESSAGE_ID,))
        self.assertEqual(recovered, ())

    async def test_report_return_port_uses_core_report_message_identity(self):
        returned: c.ReportReturn = FakeReportReturn()
        self.assertIsInstance(returned, c.ReportReturn)
        accepted = await returned.accept_report(AGENT_ID, report_dict("question"))
        processed = await returned.process_report(accepted.message_id)
        deliveries = await returned.deliver_pending(1)
        recovered = await returned.recover_inflight()
        self.assertEqual(processed.message_id, REPORT_MESSAGE_ID)
        self.assertEqual(deliveries[0].subject_id, REPORT_MESSAGE_ID)
        self.assertEqual(recovered, ())

    async def test_processor_port_can_be_faked_without_a_runtime(self):
        processor: c.MessageProcessor = FakeMessageProcessor()
        self.assertIsInstance(processor, c.MessageProcessor)
        accepted = await processor.accept_supervisor(supervisor_dict())
        processed = await processor.process_message(accepted.message_id)
        self.assertEqual(processed.state, c.ProcessingState.PROCESSED)
        self.assertIsNone(await processor.recover_once(1))


if __name__ == "__main__":
    unittest.main()
