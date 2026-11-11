"""Opt-in real Agent tests for the formal adapters.

These tests call real CLIs/services and cost tokens or channel credits. They are
skipped unless ``CF_C03_REAL_AGENT_TESTS=1``. Set ``CF_C03_EVIDENCE_DIR`` to
persist JSON evidence for the C-03 report.

Run::

    $env:CF_C03_REAL_AGENT_TESTS="1"
    $env:CF_C03_EVIDENCE_DIR="D:\\CortexFlow\\contextflow-research\\_artifacts\\c03-agent-runtime\\evidence"
    python -m pytest tests/agent_runtime/test_real_agents.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from contextflow_core.agent_runtime.config import RuntimeConfig
from contextflow_core.agent_runtime.contracts import (
    ExecutionRequest,
    ExecutionState,
    FailureKind,
    SessionPolicy,
)
from contextflow_core.agent_runtime.registry import create_registry

REAL = os.environ.get("CF_C03_REAL_AGENT_TESTS") == "1"
EVIDENCE_DIR = Path(os.environ["CF_C03_EVIDENCE_DIR"]) if os.environ.get("CF_C03_EVIDENCE_DIR") else None

pytestmark = pytest.mark.skipif(not REAL, reason="set CF_C03_REAL_AGENT_TESTS=1 to run real Agent tests")


def _registry(tmp_path: Path):
    return create_registry(RuntimeConfig(journal_root=tmp_path / "journal"))


def _record(name: str, payload: dict) -> None:
    if EVIDENCE_DIR is None:
        return
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_DIR / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _request(runtime: str, workdir: Path, instruction: str, **overrides) -> ExecutionRequest:
    values = {
        "runtime": runtime,
        "instruction": instruction,
        "workdir": str(workdir),
        "caller_ref": "cfcall_real_" + runtime.replace("-", "_"),
        "session_policy": SessionPolicy.NEW,
        "timeout_s": 240,
        "permission_profile": "workspace_write",
    }
    values.update(overrides)
    return ExecutionRequest(**values)


def _run(coro):
    return asyncio.run(coro)


async def _start_with_refusal_retry(adapter, request, attempts: int = 3):
    """Retry once or twice when the model refuses a benign file task.

    Claude Code occasionally answers "I'm sorry, but I cannot assist..." for a
    terse file-creation prompt. That is a real Agent behaviour, not an adapter
    failure: the execution still finishes and the adapter reports the refusal
    text. The test retries with a fresh caller_ref so the scenario can verify
    the adapter path deterministically.
    """
    from dataclasses import replace

    last = None
    for index in range(attempts):
        current = request if index == 0 else replace(
            request, caller_ref=f"{request.caller_ref}_retry{index}"
        )
        handle = await adapter.start(current)
        result = await adapter.wait(handle.execution_id)
        last = (handle, result)
        text = (result.final_text or "").lower()
        if "cannot assist" not in text:
            return last
    return last


def test_claude_new_session_and_resume(tmp_path: Path) -> None:
    async def scenario() -> None:
        registry = _registry(tmp_path)
        adapter = registry.get("claude-cli")
        workdir = tmp_path / "claude"
        workdir.mkdir()
        first, first_result = await _start_with_refusal_retry(
            adapter,
            _request(
                "claude-cli",
                workdir,
                "在当前隔离工作目录中创建文件 notes.txt，写入一行文本 alpha，然后读取确认并报告。",
                caller_ref="cfcall_c03_claude_1",
                timeout_s=180,
            ),
        )
        _record(
            "claude-new-session",
            {"handle": first.to_dict(), "result": first_result.to_dict()},
        )
        assert first_result.state is ExecutionState.FINISHED
        assert first_result.snapshot.native_session_id
        assert (workdir / "notes.txt").read_text(encoding="utf-8").strip() == "alpha"

        second, second_result = await _start_with_refusal_retry(
            adapter,
            _request(
                "claude-cli",
                workdir,
                "读取当前目录的 notes.txt，在文件末尾追加一行 beta，然后报告文件内容。",
                caller_ref="cfcall_c03_claude_2",
                session_policy=SessionPolicy.REUSE,
                native_session_id=first_result.snapshot.native_session_id,
                timeout_s=180,
            ),
        )
        _record(
            "claude-resume",
            {"handle": second.to_dict(), "result": second_result.to_dict()},
        )
        assert second_result.state is ExecutionState.FINISHED
        assert second_result.snapshot.native_session_id == first_result.snapshot.native_session_id
        content = (workdir / "notes.txt").read_text(encoding="utf-8")
        assert "alpha" in content and "beta" in content

    _run(scenario())


def test_dsh_new_session_and_cancel(tmp_path: Path) -> None:
    async def scenario() -> None:
        registry = _registry(tmp_path)
        adapter = registry.get("dsh")
        workdir = tmp_path / "dsh"
        workdir.mkdir()
        handle = await adapter.start(
            _request(
                "dsh",
                workdir,
                "在当前隔离目录中创建 hello.txt，内容为 ContextFlow Adapter Test，然后报告完成情况。",
                caller_ref="cfcall_c03_dsh_1",
                timeout_s=180,
            )
        )
        result = await adapter.wait(handle.execution_id)
        _record("dsh-new-session", {"handle": handle.to_dict(), "result": result.to_dict()})
        assert result.state is ExecutionState.FINISHED
        assert (workdir / "hello.txt").read_text(encoding="utf-8").strip() == "ContextFlow Adapter Test"

        cancel_handle = await adapter.start(
            _request(
                "dsh",
                workdir,
                "请从 1 开始逐个输出整数，一直输出到 100000，每个数字单独一行，不要省略。",
                caller_ref="cfcall_c03_dsh_cancel",
                timeout_s=180,
            )
        )
        time.sleep(6)
        cancel = await adapter.cancel(cancel_handle.execution_id)
        _record("dsh-cancel", cancel.to_dict())
        assert cancel.disposition.value in {"remote_confirmed_stopped", "unknown"}
        assert cancel.snapshot.state in {ExecutionState.CANCELLED, ExecutionState.UNKNOWN}
        assert cancel.snapshot.side_effects_possible is True

    _run(scenario())


def test_codex_new_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        registry = _registry(tmp_path)
        adapter = registry.get("codex-cli")
        workdir = tmp_path / "codex"
        workdir.mkdir()
        handle = await adapter.start(
            _request(
                "codex-cli",
                workdir,
                "在当前隔离目录中创建 hello.txt，内容为 ContextFlow Adapter Test，然后报告完成情况。",
                caller_ref="cfcall_c03_codex_1",
                timeout_s=300,
            )
        )
        result = await adapter.wait(handle.execution_id)
        _record("codex-new-session", {"handle": handle.to_dict(), "result": result.to_dict()})
        if result.state is ExecutionState.FINISHED:
            assert (workdir / "hello.txt").read_text(encoding="utf-8").strip() == "ContextFlow Adapter Test"
        else:
            # Account quota / channel limits are recorded, not mocked away.
            assert result.state is ExecutionState.FAILED
            assert result.failure_kind in {
                FailureKind.QUOTA_EXCEEDED,
                FailureKind.CONCURRENCY_LIMIT,
                FailureKind.AUTHENTICATION,
                FailureKind.RUNTIME_ERROR,
            }

    _run(scenario())


def test_codex_relay_new_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        registry = _registry(tmp_path)
        adapter = registry.get("codex-relay")
        workdir = tmp_path / "relay"
        workdir.mkdir()
        handle = await adapter.start(
            _request(
                "codex-relay",
                workdir,
                "在当前隔离目录中创建 hello.txt，内容为 ContextFlow Adapter Test，然后报告完成情况。",
                caller_ref="cfcall_c03_relay_1",
                timeout_s=300,
            )
        )
        result = await adapter.wait(handle.execution_id)
        _record("relay-new-session", {"handle": handle.to_dict(), "result": result.to_dict()})
        if result.state is ExecutionState.FINISHED:
            assert (workdir / "hello.txt").read_text(encoding="utf-8").strip() == "ContextFlow Adapter Test"
        else:
            assert result.state is ExecutionState.FAILED
            assert result.failure_kind in {
                FailureKind.CONCURRENCY_LIMIT,
                FailureKind.QUOTA_EXCEEDED,
                FailureKind.AUTHENTICATION,
                FailureKind.RUNTIME_ERROR,
            }

    _run(scenario())
