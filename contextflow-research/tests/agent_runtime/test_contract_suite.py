"""Unified interface conformance tests for every registered runtime."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextflow_core.agent_runtime.contracts import (
    ExecutionRequest,
    ExecutionState,
    SessionPolicy,
    TERMINAL_STATES,
)
from contextflow_core.agent_runtime.errors import (
    ExecutionNotFound,
    InvalidRequest,
    UnsupportedOperation,
)
from contextflow_core.agent_runtime.registry import RUNTIME_KINDS, create_registry


@pytest.fixture()
def registry(tmp_path: Path):
    return create_registry(_config(tmp_path))


def _config(tmp_path: Path):
    from contextflow_core.agent_runtime.config import RuntimeConfig

    return RuntimeConfig(journal_root=tmp_path / "journal")


def _request(runtime: str, tmp_path: Path, **overrides) -> ExecutionRequest:
    values = {
        "runtime": runtime,
        "instruction": "test instruction",
        "workdir": str(tmp_path),
        "caller_ref": "cfcall_conformance",
        "session_policy": SessionPolicy.NEW,
        "timeout_s": 30,
    }
    values.update(overrides)
    return ExecutionRequest(**values)


def test_registry_covers_all_runtime_kinds(registry) -> None:
    assert registry.runtimes() == RUNTIME_KINDS


def test_capability_matrix_is_json_serializable(registry) -> None:
    matrix = registry.capabilities_matrix()
    assert len(matrix) == len(RUNTIME_KINDS)
    json.dumps(matrix)
    for row in matrix:
        assert row["runtime"] in RUNTIME_KINDS
        assert row["evidence_status"] in {
            "verified",
            "partial",
            "unverified",
            "blocked",
            "unsupported",
        }


@pytest.mark.parametrize("runtime", [item for item in RUNTIME_KINDS if item != "codex-desktop"])
def test_process_and_service_adapters_reject_invalid_requests(registry, runtime, tmp_path) -> None:
    adapter = registry.get(runtime)
    with pytest.raises(InvalidRequest):
        adapter._validate_request(_request(runtime, tmp_path, workdir="relative"))
    with pytest.raises(InvalidRequest):
        adapter._validate_request(_request(runtime, tmp_path, session_policy=SessionPolicy.REUSE))


@pytest.mark.parametrize("runtime", [item for item in RUNTIME_KINDS if item != "codex-desktop"])
def test_unknown_execution_id(registry, runtime) -> None:
    adapter = registry.get(runtime)
    with pytest.raises(ExecutionNotFound):
        adapter._inspect_sync("cfexec_unknown")
    with pytest.raises(ExecutionNotFound):
        adapter._cancel_sync("cfexec_unknown")


def test_desktop_runtime_is_explicitly_unsupported(registry, tmp_path) -> None:
    import asyncio

    adapter = registry.get("codex-desktop")
    assert adapter.capabilities.supports_new_session is False
    assert adapter.capabilities.control_kind.value == "gui"
    with pytest.raises(UnsupportedOperation):
        asyncio.run(adapter.start(_request("codex-desktop", tmp_path)))


@pytest.mark.parametrize("runtime", ["codex-cli", "claude-cli", "codex-relay"])
def test_unsupported_permission_profile(registry, runtime, tmp_path) -> None:
    adapter = registry.get(runtime)
    with pytest.raises(InvalidRequest):
        adapter._validate_request(_request(runtime, tmp_path, permission_profile="root_everything"))


def test_terminal_state_vocabulary_is_complete() -> None:
    assert {state.value for state in TERMINAL_STATES} == {
        "finished",
        "failed",
        "cancelled",
        "unknown",
    }
    assert ExecutionState.PENDING.value == "pending"
    assert ExecutionState.RUNNING.value == "running"
