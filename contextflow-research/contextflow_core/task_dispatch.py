"""C-05: explicit single-Agent delivery, observation and recovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import contracts as c
from .agent_runtime import (
    AdapterError, ConcurrencyLimit, ExecutionHandle, ExecutionJournal, ExecutionNotFound,
    ExecutionRequest, ExecutionResult, ExecutionSnapshot, ExecutionState, FailureKind,
    IdempotencyConflict, InvalidRequest, RuntimeRegistry, SessionPolicy, is_terminal,
)
from .errors import ContextFlowError
from .execution_store import SQLiteExecutionRepository, TaskExecution
from .task_store import SQLiteTaskRepository


@dataclass(frozen=True)
class AgentRuntimeBinding:
    """Trusted Core configuration for one Agent. The first version always starts a new session."""

    agent_id: c.AgentId
    runtime: str
    workdir: str
    permission_profile: str = "read_only"
    timeout_s: float = 300.0

    def __post_init__(self) -> None:
        path = Path(self.workdir)
        if not path.is_absolute() or not path.is_dir():
            raise ContextFlowError("TASK_RUNTIME_CONFIG_INVALID", "workdir must be an existing absolute directory")
        if self.permission_profile not in ("read_only", "workspace_write"):
            raise ContextFlowError("TASK_RUNTIME_CONFIG_INVALID", "unsupported permission profile for Task delivery")

    def request(self, task: c.Task, delivery_id: c.DeliveryId) -> ExecutionRequest:
        return ExecutionRequest(
            runtime=self.runtime, instruction=task.content, workdir=self.workdir,
            caller_ref=str(delivery_id), session_policy=SessionPolicy.NEW,
            permission_profile=self.permission_profile, timeout_s=self.timeout_s,
        )


class TaskDeliveryDispatcher:
    def __init__(
        self, tasks: SQLiteTaskRepository, agents: c.AgentDirectory,
        runtimes: RuntimeRegistry, binding: AgentRuntimeBinding | None,
    ) -> None:
        self.tasks = tasks
        self.agents = agents
        self.runtimes = runtimes
        self.binding = binding
        self.executions = SQLiteExecutionRepository(tasks)

    async def dispatch_delivery(self, delivery_id: c.DeliveryId) -> TaskExecution:
        current = await self.get_execution(delivery_id)
        if current is not None:
            return current
        delivery = await self.tasks.get_delivery(delivery_id)
        if delivery is None:
            raise ContextFlowError("TASK_DELIVERY_NOT_FOUND", "no such TaskDelivery")
        binding = self.binding
        if binding is None or binding.agent_id != delivery.target_id:
            raise ContextFlowError("TASK_RUNTIME_NOT_CONFIGURED", "target Agent has no Runtime binding")
        agent = await self.agents.get_agent(delivery.target_id)
        if agent is None or not agent.available:
            raise ContextFlowError("TASK_AGENT_UNAVAILABLE", "target Agent is unavailable")

        adapter = self.runtimes.get(binding.runtime)
        capabilities = adapter.capabilities
        if (not capabilities.supports_new_session or not capabilities.supports_permission_profiles
                or binding.permission_profile not in capabilities.supported_permission_profiles):
            raise ContextFlowError("TASK_RUNTIME_CONFIG_INVALID", "adapter cannot enforce the configured session or permissions")
        task = await self.tasks.get_task(delivery.subject_id)
        request = binding.request(task, delivery_id)
        if not await self.executions.claim(delivery, request.runtime, request.fingerprint()):
            return await self.get_execution(delivery_id)

        try:
            handle = await adapter.start(request)
        except (InvalidRequest, ConcurrencyLimit) as exc:
            return await self.executions.record_start_error(
                delivery_id, c.ErrorInfo(code=exc.code, message=exc.message), rejected=True,
            )
        except Exception as exc:
            code = exc.code if isinstance(exc, AdapterError) else "TASK_START_UNKNOWN"
            return await self.executions.record_start_error(
                delivery_id, c.ErrorInfo(code=code, message="adapter acceptance could not be confirmed"),
            )

        if handle.runtime != request.runtime or handle.caller_ref != request.caller_ref:
            return await self.executions.record_start_error(delivery_id, c.ErrorInfo(
                code="TASK_HANDLE_MISMATCH", message="adapter returned a handle for a different request",
            ))
        return await self.executions.record_handle(delivery_id, handle)

    async def get_execution(self, delivery_id: c.DeliveryId) -> TaskExecution | None:
        """Read the last saved status without calling the Adapter."""
        return await self.executions.get(delivery_id)

    async def get_execution_result(self, delivery_id: c.DeliveryId) -> dict | None:
        execution = await self.get_execution(delivery_id)
        return execution.result if execution is not None else None

    async def refresh_execution(self, delivery_id: c.DeliveryId) -> TaskExecution | None:
        """Query the existing Adapter execution and persist its status or terminal result."""
        return await self._observe_execution(delivery_id, wait=False)

    async def wait_for_execution(self, delivery_id: c.DeliveryId) -> TaskExecution | None:
        """Use the Adapter's configured deadline and save the returned result."""
        return await self._observe_execution(delivery_id, wait=True)

    async def _observe_execution(self, delivery_id: c.DeliveryId, *, wait: bool) -> TaskExecution | None:
        execution = await self.get_execution(delivery_id)
        if execution is None or execution.execution_id is None or execution.has_final_result:
            return execution
        adapter = self.runtimes.get(execution.runtime)
        try:
            if wait:
                result = await adapter.wait(execution.execution_id)
                snapshot = result.snapshot
            else:
                snapshot = await adapter.inspect(execution.execution_id)
                result = await adapter.result(execution.execution_id) if is_terminal(snapshot.state) else None
        except ExecutionNotFound as exc:
            snapshot = ExecutionSnapshot(
                execution_id=execution.execution_id, runtime=execution.runtime, caller_ref=str(delivery_id),
                state=ExecutionState.UNKNOWN, native_session_id=execution.native_session_id,
                error={"code": exc.code, "message": "adapter no longer knows this execution"},
            )
            result = ExecutionResult(snapshot=snapshot, failure_kind=FailureKind.UNKNOWN)
        if result is not None:
            snapshot = result.snapshot
        if (snapshot.execution_id != execution.execution_id or snapshot.runtime != execution.runtime
                or snapshot.caller_ref != str(delivery_id)):
            raise ContextFlowError("TASK_EXECUTION_MISMATCH", "adapter observation does not match the stored execution")
        return await self.executions.record_observation(delivery_id, snapshot, result)

    async def recover_delivery(self, delivery_id: c.DeliveryId) -> TaskExecution | None:
        """Reconcile an interrupted start after its previous caller has stopped."""
        execution = await self.get_execution(delivery_id)
        if execution is None:
            return execution
        if execution.execution_id is not None:
            return await self.refresh_execution(delivery_id)
        if execution.state is ExecutionState.FAILED:
            return execution

        adapter = self.runtimes.get(execution.runtime)
        journal = getattr(adapter, "journal", None)
        if isinstance(journal, ExecutionJournal):
            try:
                record = journal.lookup(str(delivery_id), execution.request_fingerprint)
            except IdempotencyConflict:
                record = None
            if record is not None and record.runtime == execution.runtime:
                handle = ExecutionHandle(
                    execution_id=record.execution_id, runtime=record.runtime, caller_ref=record.caller_ref,
                    state=record.state, created_at=record.created_at, native_session_id=record.native_session_id,
                    session_policy=SessionPolicy.NEW, resumable=record.resumable, pid=record.pid,
                )
                await self.executions.record_handle(delivery_id, handle)
                return await self.refresh_execution(delivery_id)

        return await self.executions.record_start_error(delivery_id, c.ErrorInfo(
            code="TASK_START_UNKNOWN", message="no matching adapter journal record; delivery will not restart",
        ))
