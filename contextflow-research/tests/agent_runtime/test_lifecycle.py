"""Journal, process lifecycle, concurrency and idempotency tests.

These tests use a harmless local child process (``python -c sleep``) and never
call a real Agent. Async adapter calls are driven with ``asyncio.run`` so the
suite does not require pytest-asyncio.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from contextflow_core.agent_runtime.base import ProcessRuntimeAdapter
from contextflow_core.agent_runtime.contracts import (
    AgentCapabilities,
    ControlKind,
    EvidenceStatus,
    ExecutionRequest,
    ExecutionResult,
    ExecutionState,
    FailureKind,
    SessionPolicy,
    utc_now,
)
from contextflow_core.agent_runtime.errors import (
    ConcurrencyLimit,
    ExecutionNotFound,
    IdempotencyConflict,
    InvalidRequest,
)
from contextflow_core.agent_runtime.journal import ExecutionJournal, JournalRecord


class SleepAdapter(ProcessRuntimeAdapter):
    runtime = "test-sleep"
    capabilities = AgentCapabilities(
        runtime="test-sleep",
        control_kind=ControlKind.PROCESS,
        supports_new_session=True,
        supports_session_reuse=True,
        supports_resume=False,
        supports_streaming=False,
        supports_structured_result=False,
        supports_structured_failure=False,
        supports_native_cancel=False,
        supports_usage=False,
        supports_permission_profiles=False,
        supports_workspace_isolation=True,
        evidence_status=EvidenceStatus.UNVERIFIED,
    )

    def _build_argv(self, request: ExecutionRequest) -> list[str]:
        return [sys.executable, "-c", "import time; time.sleep(60)"]

    def _finalize(self, live, exit_code, killed, timed_out) -> ExecutionResult:
        state = ExecutionState.UNKNOWN if killed else ExecutionState.FINISHED
        snapshot = self._make_snapshot(live, state, exit_code, result_complete=not killed)
        return ExecutionResult(
            snapshot=snapshot,
            failure_kind=FailureKind.UNKNOWN if killed else FailureKind.NONE,
        )


def _request(tmp_path: Path, **overrides) -> ExecutionRequest:
    values = {
        "runtime": "test-sleep",
        "instruction": "sleep",
        "workdir": str(tmp_path),
        "caller_ref": "cfcall_lifecycle",
        "session_policy": SessionPolicy.NEW,
        "timeout_s": 60,
    }
    values.update(overrides)
    return ExecutionRequest(**values)


def test_start_inspect_cancel(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = SleepAdapter(tmp_path / "journal")
        handle = await adapter.start(_request(tmp_path))
        assert handle.state is ExecutionState.RUNNING
        assert handle.pid
        snapshot = await adapter.inspect(handle.execution_id)
        assert snapshot.state is ExecutionState.RUNNING
        cancel = await adapter.cancel(handle.execution_id)
        assert cancel.disposition.value == "local_process_terminated"
        assert cancel.snapshot.state is ExecutionState.UNKNOWN
        assert cancel.snapshot.side_effects_possible is True
        assert "not rolled back" in cancel.note

    asyncio.run(scenario())


def test_timeout_reports_unknown(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = SleepAdapter(tmp_path / "journal")
        handle = await adapter.start(_request(tmp_path, timeout_s=1))
        result = await adapter.wait(handle.execution_id)
        assert result.state is ExecutionState.UNKNOWN
        assert result.failure_kind is FailureKind.TIMEOUT
        assert result.cancel is not None

    asyncio.run(scenario())


def test_concurrency_limit(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = SleepAdapter(tmp_path / "journal", max_concurrency=1)
        first = await adapter.start(_request(tmp_path, caller_ref="cfcall_one"))
        with pytest.raises(ConcurrencyLimit):
            await adapter.start(_request(tmp_path, caller_ref="cfcall_two"))
        await adapter.cancel(first.execution_id)
        second = await adapter.start(_request(tmp_path, caller_ref="cfcall_three"))
        assert second.execution_id != first.execution_id
        await adapter.cancel(second.execution_id)

    asyncio.run(scenario())


def test_duplicate_caller_ref_is_suppressed(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = SleepAdapter(tmp_path / "journal")
        request = _request(tmp_path)
        first = await adapter.start(request)
        second = await adapter.start(request)
        assert first.execution_id == second.execution_id
        assert second.diagnostics.get("duplicate_suppressed") is True
        await adapter.cancel(first.execution_id)

    asyncio.run(scenario())


def test_same_caller_ref_different_request_conflicts(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = SleepAdapter(tmp_path / "journal")
        first = await adapter.start(_request(tmp_path))
        with pytest.raises(IdempotencyConflict):
            await adapter.start(_request(tmp_path, instruction="different"))
        await adapter.cancel(first.execution_id)

    asyncio.run(scenario())


def test_unknown_execution_ids(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = SleepAdapter(tmp_path / "journal")
        with pytest.raises(ExecutionNotFound):
            await adapter.inspect("cfexec_unknown")
        with pytest.raises(ExecutionNotFound):
            await adapter.cancel("cfexec_unknown")

    asyncio.run(scenario())


def test_invalid_request_rejected(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = SleepAdapter(tmp_path / "journal")
        with pytest.raises(InvalidRequest):
            await adapter.start(_request(tmp_path, workdir="relative"))
        with pytest.raises(InvalidRequest):
            await adapter.start(_request(tmp_path, session_policy=SessionPolicy.REUSE))

    asyncio.run(scenario())


def test_journal_restart_marks_non_terminal_unknown(tmp_path: Path) -> None:
    journal = ExecutionJournal(tmp_path / "journal")
    journal.register(
        JournalRecord(
            execution_id="cfexec_" + "a" * 32,
            runtime="test-sleep",
            caller_ref="cfcall_restart",
            fingerprint="f" * 64,
            state=ExecutionState.RUNNING,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
    )
    reloaded = ExecutionJournal(tmp_path / "journal")
    record = reloaded.get("cfexec_" + "a" * 32)
    assert record is not None
    assert record.state is ExecutionState.UNKNOWN
    assert "recovered_after_adapter_restart" in record.note


def test_restart_unknown_blocks_reexecution(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = SleepAdapter(tmp_path / "journal")
        request = _request(tmp_path)
        handle = await adapter.start(request)
        await adapter.cancel(handle.execution_id)
        journal = ExecutionJournal(tmp_path / "journal")
        journal.update(handle.execution_id, state=ExecutionState.RUNNING)
        restarted = SleepAdapter(tmp_path / "journal")
        with pytest.raises(IdempotencyConflict):
            await restarted.start(request)

    asyncio.run(scenario())


def test_journal_conflict_and_persistence(tmp_path: Path) -> None:
    journal = ExecutionJournal(tmp_path / "journal")
    journal.register(
        JournalRecord(
            execution_id="cfexec_" + "b" * 32,
            runtime="test-sleep",
            caller_ref="cfcall_persist",
            fingerprint="a" * 64,
            state=ExecutionState.FINISHED,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
    )
    with pytest.raises(IdempotencyConflict):
        journal.lookup("cfcall_persist", "b" * 64)
    reloaded = ExecutionJournal(tmp_path / "journal")
    assert reloaded.get("cfexec_" + "b" * 32).state is ExecutionState.FINISHED
