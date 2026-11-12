import asyncio
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from contextflow_core import contracts as c
from contextflow_core.agent_directory import ConfiguredAgentDirectory
from contextflow_core.agent_runtime import ExecutionState, RuntimeRegistry
from contextflow_core.endpoint_identity import RegistryEndpointIdentityReader
from contextflow_core.fake_parser import FakeInstructionParser
from contextflow_core.message_store import SQLiteMessageStore
from contextflow_core.parse_jobs import SQLiteParseJobRepository
from contextflow_core.parser_worker import ParserWorker
from contextflow_core.receiver import CoreReceiver
from contextflow_core.supervisor_control import SupervisorControlService
from contextflow_core.task_dispatch import AgentRuntimeBinding, TaskDeliveryDispatcher
from contextflow_core.task_store import SQLiteTaskRepository, TaskCreationService
from tests.adapter.test_bridge_protocol import BridgeTestCase, make_envelope, recv_until
from tests.core.test_task_creation import parsed
from tests.core.test_task_dispatch import FakeRuntime


class TaskReportLoopTests(BridgeTestCase):
    async def pipeline(self):
        from contextflow_core.task_reports import TaskReportService

        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        root = Path(workspace.name)
        database = root / "messages.sqlite3"
        identities = RegistryEndpointIdentityReader(self.registry_path)
        messages = SQLiteMessageStore(database, identities)
        jobs = SQLiteParseJobRepository(database)
        self.addAsyncCleanup(messages.close)
        self.addAsyncCleanup(jobs.close)
        tasks = SQLiteTaskRepository(database)
        agents = ConfiguredAgentDirectory.from_dict({
            "schema_version": 1,
            "agents": [{"agent_id": "codex-local", "name": "Codex", "aliases": [], "available": True}],
        })
        runtime = FakeRuntime(root)
        dispatcher = TaskDeliveryDispatcher(
            tasks, agents, RuntimeRegistry({"fake": runtime}),
            AgentRuntimeBinding(c.AgentId("codex-local"), "fake", str(root)),
        )
        reports = TaskReportService(tasks, messages, identities)
        receiver = CoreReceiver(
            messages, controls=SupervisorControlService(self.registry, messages, jobs, reports=reports),
        )
        receiving = asyncio.create_task(receiver.serve_connection(self.core))
        self.addAsyncCleanup(self.stop_receiver, receiving)
        source = make_envelope("sub_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "session-abc", self.binding.endpoint_id)
        await self.page.send(json.dumps({"type": "adapter_submission", "request_id": "instruction", "envelope": source}))
        ack = await recv_until(self.page, lambda msg: msg.get("request_id") == "instruction")
        self.assertEqual(ack["status"], "accepted")
        message = await messages.get_message_by_submission_id(c.SubmissionId(source["submission_id"]))
        await ParserWorker(repository=jobs, messages=messages, parser=FakeInstructionParser([parsed("Codex")])).run_once()
        batch = await TaskCreationService(tasks, jobs, agents).create_tasks_from_parse_result(message.message_id)
        task, delivery = batch.tasks[0], batch.deliveries[0]
        await dispatcher.dispatch_delivery(delivery.delivery_id)
        runtime.state = ExecutionState.FINISHED
        await dispatcher.wait_for_execution(delivery.delivery_id)
        return tasks, messages, reports, receiver, task, delivery

    async def stop_receiver(self, receiving):
        receiving.cancel()
        await asyncio.gather(receiving, return_exceptions=True)

    async def reply_to_send(self, request, **data):
        await self.page.send(json.dumps({
            "type": "bridge_result", "request_id": request["id"],
            "result": {"id": request["id"], "type": "result", "success": True, "data": data},
        }))

    async def review(self, report, task, *, endpoint, session, code, decision="request_changes"):
        await self.page.send(json.dumps({
            "type": "supervisor_control_request", "request_id": "review", "action": "submit_report_review",
            "params": {"report_id": report.report_id, "task_id": task.task_id,
                       "endpoint_id": endpoint, "native_session_id": session,
                       "review_code": code, "decision": decision, "note": "check the source"},
        }))
        return await recv_until(self.page, lambda msg: msg.get("request_id") == "review")

    async def test_persist_send_and_review_through_existing_bridge(self):
        tasks, messages, reports, receiver, task, delivery = await self.pipeline()
        wrong, _ = self.registry.register("chatgpt_web", "different-session")
        report = await reports.generate_report(delivery.delivery_id)
        self.assertEqual((await reports.generate_report(delivery.delivery_id)).message_id, report.message_id)
        self.assertIsNone(report.review_decision)
        self.assertEqual((await messages.get_message(report.message_id)).envelope.content.text, "agent result")
        sending = asyncio.create_task(reports.send_report(report.report_id, receiver))
        request = (await recv_until(self.page, lambda msg: msg.get("type") == "bridge_request"))["request"]
        self.assertEqual(request["endpoint_id"], self.binding.endpoint_id)
        self.assertEqual(request["session_id"], self.binding.native_session_id)
        self.assertFalse(request["force"])
        self.assertIn(task.task_id, request["text"])
        code = re.search(r"审核码: (\S+)", request["text"]).group(1)
        self.assertIs((await reports.get_report(report.report_id)).delivery.state, c.DeliveryState.SENDING)
        self.assertIsNotNone(await messages.get_message(report.message_id))
        # Even while browser verification is outstanding, ingress ACKs must still flow.
        extra = make_envelope("sub_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "different-session", wrong.endpoint_id)
        await self.page.send(json.dumps({"type": "adapter_submission", "request_id": "extra", "envelope": extra}))
        self.assertEqual((await recv_until(self.page, lambda msg: msg.get("request_id") == "extra"))["status"], "accepted")
        await self.reply_to_send(request, sent=True, verified=True, session_id="session-abc", user_message_id="user-report")
        sent = await sending
        self.assertIs(sent.delivery.state, c.DeliveryState.CONFIRMED)
        self.assertIsNone(sent.review_decision)
        self.assertEqual((await reports.send_report(report.report_id, receiver)).delivery, sent.delivery)
        denied = await self.review(report, task, endpoint=wrong.endpoint_id, session="different-session", code=code)
        self.assertFalse(denied["success"])
        invalid_code = await self.review(report, task, endpoint=self.binding.endpoint_id, session="session-abc", code="incorrect")
        self.assertEqual(invalid_code["error"]["code"], "REPORT_REVIEW_DENIED")
        accepted = await self.review(
            report, task, endpoint=self.binding.endpoint_id, session="session-abc", code=code, decision="accept",
        )
        self.assertTrue(accepted["success"], accepted)
        reopened = type(reports)(tasks, messages, RegistryEndpointIdentityReader(self.registry_path))
        reviewed = await reopened.get_report(report.report_id)
        self.assertEqual(reviewed.review_decision.value, "accept")
        self.assertEqual(reviewed.review_note, "check the source")
        self.assertIs((await tasks.get_task(task.task_id)).state, c.TaskState.DISPATCHED)
        self.assertEqual((await messages.get_message(report.message_id)).envelope.content.text, "agent result")
        changed = await self.review(report, task, endpoint=self.binding.endpoint_id, session="session-abc", code=code)
        self.assertEqual(changed["error"]["code"], "REPORT_REVIEW_CONFLICT")

    async def test_unverified_reply_is_unknown_and_is_not_resent(self):
        _, _, reports, receiver, _, delivery = await self.pipeline()
        report = await reports.generate_report(delivery.delivery_id)
        sending = asyncio.create_task(reports.send_report(report.report_id, receiver))
        request = (await recv_until(self.page, lambda msg: msg.get("type") == "bridge_request"))["request"]
        await self.page.send(json.dumps({
            "type": "bridge_result", "request_id": request["id"],
            "result": {"id": request["id"], "type": "result", "success": False,
                       "error": {"code": "SESSION_NOT_FOUND", "message": "session is closed"}},
        }))
        self.assertIs((await sending).delivery.state, c.DeliveryState.PENDING)
        sending = asyncio.create_task(reports.send_report(report.report_id, receiver))
        request = (await recv_until(self.page, lambda msg: msg.get("type") == "bridge_request"))["request"]
        await self.reply_to_send(request, sent=True, verified=True, session_id="wrong-session", user_message_id="foreign")
        unknown = await sending
        self.assertIs(unknown.delivery.state, c.DeliveryState.UNKNOWN)
        repeated = await reports.send_report(report.report_id, receiver)
        self.assertEqual(repeated.delivery, unknown.delivery)

    async def test_interrupted_send_is_saved_as_unknown(self):
        _, _, reports, receiver, _, delivery = await self.pipeline()
        report = await reports.generate_report(delivery.delivery_id)
        sending = asyncio.create_task(reports.send_report(report.report_id, receiver))
        await recv_until(self.page, lambda msg: msg.get("type") == "bridge_request")
        sending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await sending
        self.assertIs((await reports.get_report(report.report_id)).delivery.state, c.DeliveryState.UNKNOWN)
