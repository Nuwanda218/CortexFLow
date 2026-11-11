"""Parser tests replaying real C-02 evidence through the formal adapters.

The fixtures are recorded outputs from the C-02 real executions (see
``fixtures/build_dsh_fixtures.py``). No real Agent is called here.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from contextflow_core.agent_runtime.adapters.claude_cli import ClaudeCliAdapter
from contextflow_core.agent_runtime.adapters.codex_cli import CodexCliAdapter, read_codex_jsonl
from contextflow_core.agent_runtime.adapters.dsh import DshExecution, DshRuntimeAdapter, _last_turn
from contextflow_core.agent_runtime.base import LiveExecution
from contextflow_core.agent_runtime.contracts import (
    ExecutionHandle,
    ExecutionRequest,
    ExecutionState,
    FailureKind,
    SessionPolicy,
    utc_now,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class _FakeProc:
    def __init__(self, exit_code: int | None) -> None:
        self._exit_code = exit_code

    def poll(self) -> int | None:
        return self._exit_code


def _request(workdir: Path, runtime: str = "codex-cli") -> ExecutionRequest:
    return ExecutionRequest(
        runtime=runtime,
        instruction="test",
        workdir=str(workdir),
        caller_ref="cfcall_parser",
        session_policy=SessionPolicy.NEW,
        timeout_s=60,
    )


def _live(adapter, request: ExecutionRequest, exit_code: int | None = 0) -> LiveExecution:
    handle = ExecutionHandle(
        execution_id="cfexec_" + "d" * 32,
        runtime=adapter.runtime,
        caller_ref=request.caller_ref,
        state=ExecutionState.RUNNING,
        created_at=utc_now(),
        diagnostics={"argv": []},
    )
    return LiveExecution(
        execution_id=handle.execution_id,
        proc=_FakeProc(exit_code),
        handle=handle,
        request=request,
        started_wall=time.time(),
        started_mono=time.monotonic(),
        deadline_mono=time.monotonic() + 60,
    )


def _feed(adapter, live: LiveExecution, events: list[dict]) -> None:
    for event in events:
        live.lines.append(event)
        adapter._on_event(live, event)


# ---------------------------------------------------------------------------
# Codex CLI
# ---------------------------------------------------------------------------


def test_codex_completed_run(tmp_path: Path) -> None:
    adapter = CodexCliAdapter(tmp_path / "journal")
    request = _request(tmp_path)
    live = _live(adapter, request, exit_code=0)
    _feed(adapter, live, read_codex_jsonl(FIXTURES / "codex_completed.jsonl"))
    result = adapter._finalize(live, 0, killed=False, timed_out=False)
    assert result.state is ExecutionState.FINISHED
    assert result.failure_kind is FailureKind.NONE
    assert result.snapshot.result_complete is True
    assert result.snapshot.native_session_id.startswith("01a1")
    assert "hello.txt" in (result.final_text or "")
    assert result.snapshot.usage["input_tokens"] > 0


def test_codex_business_failure_is_not_adapter_failure(tmp_path: Path) -> None:
    adapter = CodexCliAdapter(tmp_path / "journal")
    request = _request(tmp_path)
    live = _live(adapter, request, exit_code=0)
    _feed(adapter, live, read_codex_jsonl(FIXTURES / "codex_business_failure.jsonl"))
    result = adapter._finalize(live, 0, killed=False, timed_out=False)
    # The tool failed, but the execution finished and the Agent reported it.
    assert result.state is ExecutionState.FINISHED
    assert result.failure_kind is FailureKind.NONE
    assert result.snapshot.diagnostics["tool_events"]


def test_codex_turn_failed_concurrency(tmp_path: Path) -> None:
    adapter = CodexCliAdapter(tmp_path / "journal")
    request = _request(tmp_path)
    live = _live(adapter, request, exit_code=1)
    _feed(adapter, live, read_codex_jsonl(FIXTURES / "codex_concurrency_403.jsonl"))
    result = adapter._finalize(live, 1, killed=False, timed_out=False)
    assert result.state is ExecutionState.FAILED
    assert result.failure_kind is FailureKind.CONCURRENCY_LIMIT
    assert "403" in result.snapshot.error["message"]


def test_codex_killed_is_unknown(tmp_path: Path) -> None:
    adapter = CodexCliAdapter(tmp_path / "journal")
    request = _request(tmp_path)
    live = _live(adapter, request, exit_code=-1)
    _feed(adapter, live, read_codex_jsonl(FIXTURES / "codex_killed.jsonl"))
    result = adapter._finalize(live, -1, killed=True, timed_out=False)
    assert result.state is ExecutionState.UNKNOWN
    assert result.failure_kind is FailureKind.UNKNOWN
    assert result.snapshot.side_effects_possible is True
    assert result.snapshot.retry_safe is False
    assert result.snapshot.result_complete is False


def test_codex_exit_zero_without_terminal_event_is_unknown(tmp_path: Path) -> None:
    adapter = CodexCliAdapter(tmp_path / "journal")
    request = _request(tmp_path)
    live = _live(adapter, request, exit_code=0)
    _feed(adapter, live, read_codex_jsonl(FIXTURES / "codex_killed.jsonl"))
    result = adapter._finalize(live, 0, killed=False, timed_out=False)
    assert result.state is ExecutionState.UNKNOWN
    assert result.failure_kind is FailureKind.PROTOCOL_ERROR


# ---------------------------------------------------------------------------
# Claude Code CLI
# ---------------------------------------------------------------------------


def _claude_events(path: Path) -> list[dict]:
    import json

    text = path.read_text(encoding="utf-8", errors="replace").strip()
    return [json.loads(text)] if text else []


def test_claude_completed_run(tmp_path: Path) -> None:
    adapter = ClaudeCliAdapter(tmp_path / "journal")
    request = _request(tmp_path, runtime="claude-cli")
    live = _live(adapter, request, exit_code=0)
    _feed(adapter, live, _claude_events(FIXTURES / "claude_completed.json"))
    result = adapter._finalize(live, 0, killed=False, timed_out=False)
    assert result.state is ExecutionState.FINISHED
    assert result.snapshot.native_session_id == "29d29ca6-863a-458d-9406-546e0543c480"
    assert result.snapshot.usage["total_cost_usd"] > 0
    assert result.snapshot.diagnostics["permission_denials"]


def test_claude_business_failure_is_not_adapter_failure(tmp_path: Path) -> None:
    adapter = ClaudeCliAdapter(tmp_path / "journal")
    request = _request(tmp_path, runtime="claude-cli")
    live = _live(adapter, request, exit_code=0)
    _feed(adapter, live, _claude_events(FIXTURES / "claude_business_failure.json"))
    result = adapter._finalize(live, 0, killed=False, timed_out=False)
    assert result.state is ExecutionState.FINISHED
    assert result.failure_kind is FailureKind.NONE
    assert "不存在" in (result.final_text or "")


def test_claude_killed_is_unknown(tmp_path: Path) -> None:
    adapter = ClaudeCliAdapter(tmp_path / "journal")
    request = _request(tmp_path, runtime="claude-cli")
    live = _live(adapter, request, exit_code=-1)
    result = adapter._finalize(live, -1, killed=True, timed_out=False)
    assert result.state is ExecutionState.UNKNOWN
    assert result.snapshot.result_complete is False


# ---------------------------------------------------------------------------
# DSH
# ---------------------------------------------------------------------------


def test_dsh_completed_history(tmp_path: Path) -> None:
    import json

    page = json.loads((FIXTURES / "dsh_history_completed.json").read_text(encoding="utf-8"))
    turn_end, final_text = _last_turn(page)
    assert turn_end is not None
    assert turn_end["data"]["reason"]["kind"] == "completed"
    assert final_text and "hello.txt" in final_text


def test_dsh_finalize_completed(tmp_path: Path) -> None:
    adapter = DshRuntimeAdapter(tmp_path / "journal")
    request = ExecutionRequest(
        runtime="dsh",
        instruction="test",
        workdir=str(tmp_path),
        caller_ref="cfcall_dsh",
        session_policy=SessionPolicy.NEW,
        timeout_s=60,
    )
    handle = ExecutionHandle(
        execution_id="cfexec_" + "e" * 32,
        runtime="dsh",
        caller_ref=request.caller_ref,
        state=ExecutionState.RUNNING,
        created_at=utc_now(),
        native_session_id="session-test",
    )
    live = DshExecution(
        execution_id=handle.execution_id,
        session_id="session-test",
        request=request,
        request_id="req-1",
        turn_number=1,
        started_wall=time.time(),
        handle=handle,
    )
    turn_end = {"type": "turn/end", "data": {"turn": 1, "reason": {"kind": "completed"}}}
    result = adapter._finalize(live, turn_end, "done", {"tokenUsage": {"outputTokens": 1}})
    assert result.state is ExecutionState.FINISHED
    assert result.failure_kind is FailureKind.NONE
    assert result.snapshot.result_complete is True


def test_dsh_finalize_aborted_is_cancelled(tmp_path: Path) -> None:
    adapter = DshRuntimeAdapter(tmp_path / "journal")
    request = ExecutionRequest(
        runtime="dsh",
        instruction="test",
        workdir=str(tmp_path),
        caller_ref="cfcall_dsh2",
        session_policy=SessionPolicy.NEW,
        timeout_s=60,
    )
    handle = ExecutionHandle(
        execution_id="cfexec_" + "f" * 32,
        runtime="dsh",
        caller_ref=request.caller_ref,
        state=ExecutionState.RUNNING,
        created_at=utc_now(),
        native_session_id="session-test",
    )
    live = DshExecution(
        execution_id=handle.execution_id,
        session_id="session-test",
        request=request,
        request_id="req-2",
        turn_number=1,
        started_wall=time.time(),
        handle=handle,
    )
    turn_end = {
        "type": "turn/end",
        "data": {"turn": 1, "reason": {"kind": "aborted", "reason": {"kind": "user"}}},
    }
    result = adapter._finalize(live, turn_end, None, {})
    assert result.state is ExecutionState.CANCELLED
    assert result.failure_kind is FailureKind.CANCELLED
    assert result.snapshot.side_effects_possible is True
