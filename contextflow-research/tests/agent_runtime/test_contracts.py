"""Contract validation tests for the formal Agent Runtime layer."""

from __future__ import annotations

import json

import pytest

from contextflow_core.agent_runtime.contracts import (
    AgentCapabilities,
    CancelDisposition,
    CancelResult,
    ControlKind,
    EvidenceStatus,
    ExecutionHandle,
    ExecutionRequest,
    ExecutionResult,
    ExecutionSnapshot,
    ExecutionState,
    FailureKind,
    SessionPolicy,
    is_terminal,
    utc_now,
)
from contextflow_core.agent_runtime.errors import InvalidRequest


def _request(**overrides) -> ExecutionRequest:
    values = {
        "runtime": "codex-cli",
        "instruction": "create hello.txt",
        "workdir": "C:/tmp/c03-test",
        "caller_ref": "cfcall_abc123",
        "session_policy": SessionPolicy.NEW,
        "timeout_s": 60,
    }
    values.update(overrides)
    return ExecutionRequest(**values)


def test_request_accepts_valid_new_session() -> None:
    request = _request()
    assert request.runtime == "codex-cli"
    assert request.session_policy is SessionPolicy.NEW


@pytest.mark.parametrize(
    "overrides",
    [
        {"instruction": ""},
        {"instruction": "   "},
        {"workdir": "relative/path"},
        {"caller_ref": ""},
        {"timeout_s": 0},
        {"timeout_s": -1},
        {"timeout_s": 100_000},
        {"session_policy": SessionPolicy.REUSE},
        {"session_policy": SessionPolicy.NEW, "native_session_id": "abc"},
    ],
)
def test_request_rejects_invalid_fields(overrides) -> None:
    with pytest.raises(InvalidRequest):
        _request(**overrides)


def test_request_reuse_requires_session() -> None:
    request = _request(session_policy=SessionPolicy.REUSE, native_session_id="session-1")
    assert request.native_session_id == "session-1"


def test_fingerprint_is_stable_and_content_sensitive() -> None:
    first = _request()
    second = _request()
    assert first.fingerprint() == second.fingerprint()
    changed = _request(instruction="different instruction")
    assert changed.fingerprint() != first.fingerprint()
    # timeout/metadata change how it runs, not what was asked
    assert _request(timeout_s=10).fingerprint() == first.fingerprint()


def test_handle_round_trip() -> None:
    handle = ExecutionHandle(
        execution_id="cfexec_" + "a" * 32,
        runtime="codex-cli",
        caller_ref="cfcall_abc123",
        state=ExecutionState.RUNNING,
        created_at=utc_now(),
        native_session_id="thread-1",
        resumable=True,
        pid=1234,
        diagnostics={"argv": ["codex"]},
    )
    payload = handle.to_dict()
    assert json.loads(json.dumps(payload))["state"] == "running"
    assert payload["native_session_id"] == "thread-1"


def test_snapshot_and_result_round_trip() -> None:
    snapshot = ExecutionSnapshot(
        execution_id="cfexec_" + "b" * 32,
        runtime="claude-cli",
        caller_ref="cfcall_abc123",
        state=ExecutionState.FINISHED,
        native_state="success",
        native_session_id="session-1",
        exit_code=0,
        output_text="done",
        usage={"total_cost_usd": 0.1},
        result_complete=True,
    )
    result = ExecutionResult(snapshot=snapshot, failure_kind=FailureKind.NONE, final_text="done")
    payload = result.to_dict()
    assert payload["snapshot"]["state"] == "finished"
    assert payload["failure_kind"] == "none"
    assert payload["snapshot"]["result_complete"] is True


def test_cancel_result_round_trip() -> None:
    snapshot = ExecutionSnapshot(
        execution_id="cfexec_" + "c" * 32,
        runtime="dsh",
        caller_ref="cfcall_abc123",
        state=ExecutionState.CANCELLED,
        result_complete=True,
    )
    cancel = CancelResult(
        execution_id=snapshot.execution_id,
        disposition=CancelDisposition.REMOTE_CONFIRMED_STOPPED,
        requested_at=utc_now(),
        confirmed_at=utc_now(),
        snapshot=snapshot,
        note="aborted by user",
    )
    payload = cancel.to_dict()
    assert payload["disposition"] == "remote_confirmed_stopped"
    assert payload["snapshot"]["state"] == "cancelled"


def test_terminal_states() -> None:
    assert is_terminal(ExecutionState.FINISHED)
    assert is_terminal(ExecutionState.FAILED)
    assert is_terminal(ExecutionState.CANCELLED)
    assert is_terminal(ExecutionState.UNKNOWN)
    assert not is_terminal(ExecutionState.PENDING)
    assert not is_terminal(ExecutionState.RUNNING)


def test_capabilities_validation_and_serialization() -> None:
    capabilities = AgentCapabilities(
        runtime="codex-cli",
        control_kind=ControlKind.PROCESS,
        supports_new_session=True,
        supports_session_reuse=True,
        supports_resume=True,
        supports_streaming=True,
        supports_structured_result=True,
        supports_structured_failure=True,
        supports_native_cancel=False,
        supports_usage=True,
        supports_permission_profiles=True,
        supports_workspace_isolation=True,
        supported_permission_profiles=("read_only", "workspace_write"),
        evidence_status=EvidenceStatus.VERIFIED,
    )
    payload = capabilities.to_dict()
    assert payload["evidence_status"] == "verified"
    assert payload["supported_permission_profiles"] == ["read_only", "workspace_write"]


def test_capabilities_require_profiles_when_supported() -> None:
    with pytest.raises(InvalidRequest):
        AgentCapabilities(
            runtime="x",
            control_kind=ControlKind.PROCESS,
            supports_new_session=True,
            supports_session_reuse=False,
            supports_resume=False,
            supports_streaming=False,
            supports_structured_result=False,
            supports_structured_failure=False,
            supports_native_cancel=False,
            supports_usage=False,
            supports_permission_profiles=True,
            supports_workspace_isolation=False,
        )
