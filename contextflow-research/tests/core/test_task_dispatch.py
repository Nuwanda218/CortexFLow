import asyncio
from types import SimpleNamespace

from contextflow_core import contracts as c
from contextflow_core.agent_runtime import (
    ExecutionHandle, ExecutionJournal, ExecutionResult, ExecutionSnapshot, ExecutionState,
    InvalidRequest, JournalRecord, ProtocolError, RuntimeRegistry, SessionPolicy, utc_now,
)
from contextflow_core.errors import ContextFlowError
from contextflow_core.execution_store import SQLiteExecutionRepository
from contextflow_core.task_dispatch import AgentRuntimeBinding, TaskDeliveryDispatcher
from tests.core.test_task_creation import TaskPipelineCase, parsed


class FakeRuntime:
    runtime = "fake"
    capabilities = SimpleNamespace(
        supports_new_session=True, supports_permission_profiles=True,
        supported_permission_profiles=("read_only",),
    )

    def __init__(self, root, *, start_error=None):
        self.journal = ExecutionJournal(root / "fake-journal")
        self.requests = []
        self.waited = []
        self.start_error = start_error
        self.state = ExecutionState.RUNNING
        self.output = "agent result"

    async def start(self, request):
        self.requests.append(request)
        if self.start_error:
            raise self.start_error
        return ExecutionHandle(
            execution_id="fake-execution", runtime=self.runtime, caller_ref=request.caller_ref,
            state=self.state, created_at=utc_now(), native_session_id="fake-session",
            session_policy=SessionPolicy.NEW,
        )

    async def inspect(self, execution_id):
        return ExecutionSnapshot(
            execution_id=execution_id, runtime=self.runtime, caller_ref=self.requests[0].caller_ref,
            state=self.state, native_session_id="fake-session",
            output_text=self.output if self.state is ExecutionState.FINISHED else None,
            result_complete=self.state is ExecutionState.FINISHED,
            usage={"tokens": 7},
        )

    async def result(self, execution_id):
        return ExecutionResult(snapshot=await self.inspect(execution_id), final_text=self.output)

    async def wait(self, execution_id):
        self.waited.append(execution_id)
        return await self.result(execution_id)


class TaskDispatchTests(TaskPipelineCase):
    async def pipeline(self, *, start_error=None, configure=True, targets=("Codex",)):
        source = await self.submit_and_parse(parsed(*targets))
        tasks, creation = self.service()
        batch = await creation.create_tasks_from_parse_result(source)
        runtime = FakeRuntime(self.root, start_error=start_error)
        binding = AgentRuntimeBinding(c.AgentId("codex-local"), "fake", str(self.root)) if configure else None
        dispatcher = TaskDeliveryDispatcher(tasks, self.directory, RuntimeRegistry({"fake": runtime}), binding)
        return tasks, batch, runtime, dispatcher

    async def test_dispatch_once_and_persist_the_agent_result(self):
        tasks, batch, runtime, dispatcher = await self.pipeline(targets=("Codex", "Codex"))
        task, delivery = batch.tasks[0], batch.deliveries[0]
        await asyncio.gather(
            dispatcher.dispatch_delivery(delivery.delivery_id),
            dispatcher.dispatch_delivery(delivery.delivery_id),
        )
        execution = await dispatcher.get_execution(delivery.delivery_id)
        self.assertEqual(execution.execution_id, "fake-execution")
        self.assertEqual(execution.native_session_id, "fake-session")
        self.assertIs(execution.state, ExecutionState.RUNNING)
        self.assertEqual(len(runtime.requests), 1)
        request = runtime.requests[0]
        self.assertEqual(request.caller_ref, delivery.delivery_id)
        self.assertEqual(request.instruction, task.content)
        self.assertEqual(request.workdir, str(self.root))
        self.assertIs(request.session_policy, SessionPolicy.NEW)
        self.assertEqual(request.permission_profile, "read_only")
        self.assertIs((await tasks.get_delivery(delivery.delivery_id)).state, c.DeliveryState.CONFIRMED)
        with self.assertRaisesRegex(ContextFlowError, "TASK_RUNTIME_BUSY"):
            await dispatcher.dispatch_delivery(batch.deliveries[1].delivery_id)

        runtime.state = ExecutionState.FINISHED
        runtime.output = "result " * 12_000
        await dispatcher.wait_for_execution(delivery.delivery_id)
        self.assertEqual(runtime.waited, ["fake-execution"])
        reopened = SQLiteExecutionRepository(tasks)
        result = (await reopened.get(delivery.delivery_id)).result
        self.assertEqual(result["state"], "finished")
        self.assertEqual(result["usage"], {"tokens": 7})
        self.assertTrue(result["result_complete"])
        self.assertEqual((self.root / result["output_ref"]).read_text(encoding="utf-8"), runtime.output)
        self.assertIs((await tasks.get_task(task.task_id)).state, c.TaskState.DISPATCHED)

    async def test_missing_binding_leaves_delivery_pending(self):
        tasks, batch, runtime, dispatcher = await self.pipeline(configure=False)
        delivery_id = batch.deliveries[0].delivery_id
        with self.assertRaisesRegex(ContextFlowError, "TASK_RUNTIME_NOT_CONFIGURED"):
            await dispatcher.dispatch_delivery(delivery_id)
        self.assertFalse(runtime.requests)
        self.assertIs((await tasks.get_delivery(delivery_id)).state, c.DeliveryState.PENDING)

    async def test_definite_rejection_is_failed_without_retry(self):
        tasks, batch, runtime, dispatcher = await self.pipeline(start_error=InvalidRequest("unsupported request"))
        delivery_id = batch.deliveries[0].delivery_id
        await dispatcher.dispatch_delivery(delivery_id)
        await dispatcher.dispatch_delivery(delivery_id)
        self.assertIs((await tasks.get_delivery(delivery_id)).state, c.DeliveryState.FAILED)
        self.assertEqual(len(runtime.requests), 1)

    async def test_ambiguous_start_reconciles_by_journal_without_restarting(self):
        tasks, batch, runtime, dispatcher = await self.pipeline(start_error=ProtocolError("lost response"))
        delivery_id = batch.deliveries[0].delivery_id
        execution = await dispatcher.dispatch_delivery(delivery_id)
        self.assertIs(execution.state, ExecutionState.UNKNOWN)
        self.assertIs((await tasks.get_delivery(delivery_id)).state, c.DeliveryState.UNKNOWN)
        self.assertIs((await dispatcher.recover_delivery(delivery_id)).state, ExecutionState.UNKNOWN)

        request = runtime.requests[0]
        now = utc_now()
        runtime.journal.register(JournalRecord(
            execution_id="recovered", runtime="fake", caller_ref=request.caller_ref,
            fingerprint=request.fingerprint(), state=ExecutionState.RUNNING,
            created_at=now, updated_at=now, native_session_id="fake-session",
        ))
        recovered = await dispatcher.recover_delivery(delivery_id)
        self.assertEqual(recovered.execution_id, "recovered")
        self.assertIs(recovered.state, ExecutionState.RUNNING)
        await dispatcher.dispatch_delivery(delivery_id)
        self.assertEqual(len(runtime.requests), 1)

    async def test_cancelled_start_preserves_intent_for_recovery(self):
        tasks, batch, runtime, dispatcher = await self.pipeline(start_error=asyncio.CancelledError())
        delivery_id = batch.deliveries[0].delivery_id
        with self.assertRaises(asyncio.CancelledError):
            await dispatcher.dispatch_delivery(delivery_id)
        self.assertIs((await tasks.get_delivery(delivery_id)).state, c.DeliveryState.SENDING)
        self.assertIs((await dispatcher.recover_delivery(delivery_id)).state, ExecutionState.UNKNOWN)
        await dispatcher.dispatch_delivery(delivery_id)
        self.assertEqual(len(runtime.requests), 1)
