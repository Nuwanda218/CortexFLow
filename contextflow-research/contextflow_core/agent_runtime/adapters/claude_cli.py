"""Claude Code CLI runtime adapter.

Verified against Claude Code 2.1.322 (C-02 evidence). Native mapping:

* ``--session-id <uuid>``  -> caller-chosen native session identity
* ``--resume <uuid>``      -> continue the same session with context
* ``--output-format json`` -> one result object with session_id, is_error,
  subtype, result, duration_ms, total_cost_usd, usage, permission_denials
* process killed           -> no result object; unknown outcome

A CLI success (exit 0, is_error=false, subtype=success) does not prove the task
goal was met; the adapter reports execution facts only.
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any

from ..base import LiveExecution, ProcessRuntimeAdapter
from ..contracts import (
    AgentCapabilities,
    ControlKind,
    EvidenceStatus,
    ExecutionRequest,
    ExecutionResult,
    ExecutionState,
    FailureKind,
    SessionPolicy,
)
from ..errors import InvalidRequest
from .codex_cli import classify_runtime_error

PERMISSION_PROFILES: dict[str, tuple[str, str]] = {
    "read_only": ("acceptEdits", "Read,Glob,Grep"),
    "workspace_write": ("acceptEdits", "Read,Glob,Grep,Write,Edit"),
    "full_access": ("bypassPermissions", "Read,Glob,Grep,Write,Edit,Bash"),
}


class ClaudeCliAdapter(ProcessRuntimeAdapter):
    runtime = "claude-cli"
    capabilities = AgentCapabilities(
        runtime="claude-cli",
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
        supported_permission_profiles=tuple(PERMISSION_PROFILES),
        max_concurrency=None,
        evidence_status=EvidenceStatus.VERIFIED,
        notes=(
            "--print --output-format json returns session_id, cost and token usage",
            "--session-id lets the caller choose the native session identity",
            "--resume continues the same session with context",
            "foreground -p has no native cancel; kill reports unknown",
            "exit 0 / is_error=false does not prove the task goal was met",
            "permission_denials lists tools the policy refused",
            "background sessions (--bg) have claude stop/logs, not used here",
        ),
    )

    def __init__(
        self,
        journal_dir: str | Path,
        command: list[str] | None = None,
        env: dict[str, str] | None = None,
        max_concurrency: int | None = None,
    ) -> None:
        super().__init__(
            journal_dir,
            command=command or ["claude"],
            env=env,
            max_concurrency=max_concurrency,
        )
        self._pending_sessions: dict[str, str] = {}
        self._pending_lock = threading.Lock()

    # -- argv -------------------------------------------------------------
    def _build_argv(self, request: ExecutionRequest) -> list[str]:
        mode_tools = PERMISSION_PROFILES.get(request.permission_profile)
        if mode_tools is None:
            raise InvalidRequest(
                f"unsupported permission profile {request.permission_profile!r}",
                details={"supported": list(PERMISSION_PROFILES)},
            )
        mode, tools = mode_tools
        argv = list(self.command) + [
            "-p",
            request.instruction,
            "--output-format",
            "json",
            "--permission-mode",
            mode,
            "--allowedTools",
            tools,
        ]
        resume_id = self._resume_session_id(request)
        if resume_id:
            argv += ["--resume", resume_id]
        else:
            session_id = str(uuid.uuid4())
            with self._pending_lock:
                self._pending_sessions[request.caller_ref] = session_id
            argv += ["--session-id", session_id]
        return argv

    @staticmethod
    def _resume_session_id(request: ExecutionRequest) -> str | None:
        if request.session_policy in (SessionPolicy.REUSE, SessionPolicy.AUTO):
            return request.native_session_id
        return None

    def _pre_session_id(self, request: ExecutionRequest) -> str | None:
        resume_id = self._resume_session_id(request)
        if resume_id:
            return resume_id
        with self._pending_lock:
            return self._pending_sessions.pop(request.caller_ref, None)

    # -- events -----------------------------------------------------------
    def _on_event(self, live: LiveExecution, event: dict[str, Any]) -> None:
        if event.get("type") != "result":
            return
        session_id = event.get("session_id")
        if isinstance(session_id, str) and session_id:
            live.native_session_id = session_id
        live.native_state = str(event.get("subtype") or event.get("terminal_reason") or "result")
        result_text = event.get("result")
        if isinstance(result_text, str):
            live.output_text = result_text
        live.usage = {
            "total_cost_usd": event.get("total_cost_usd"),
            "usage": event.get("usage"),
            "num_turns": event.get("num_turns"),
            "duration_ms": event.get("duration_ms"),
            "stop_reason": event.get("stop_reason"),
        }
        if event.get("is_error"):
            live.error = {
                "code": "AGENT_RUNTIME_ERROR",
                "message": str(result_text or event.get("subtype") or "claude reported an error"),
            }

    # -- terminal mapping -------------------------------------------------
    def _finalize(
        self,
        live: LiveExecution,
        exit_code: int | None,
        killed: bool,
        timed_out: bool,
    ) -> ExecutionResult:
        with live.lock:
            lines = list(live.lines)
        result = next(
            (event for event in reversed(lines) if event.get("type") == "result"),
            None,
        )
        diagnostics: dict[str, Any] = {}
        if result is not None:
            diagnostics = {
                "subtype": result.get("subtype"),
                "is_error": result.get("is_error"),
                "stop_reason": result.get("stop_reason"),
                "terminal_reason": result.get("terminal_reason"),
                "num_turns": result.get("num_turns"),
                "duration_ms": result.get("duration_ms"),
                "total_cost_usd": result.get("total_cost_usd"),
                "permission_denials": result.get("permission_denials"),
            }
            session_id = result.get("session_id")
            if isinstance(session_id, str) and session_id:
                live.native_session_id = session_id
        if killed:
            snapshot = self._make_snapshot(
                live,
                ExecutionState.UNKNOWN,
                exit_code,
                result_complete=False,
                error={
                    "code": "EXECUTION_OUTCOME_UNKNOWN",
                    "message": "process tree terminated; no result object was produced",
                },
                native_state="killed",
                diagnostics=diagnostics,
            )
            return ExecutionResult(
                snapshot=snapshot,
                failure_kind=FailureKind.TIMEOUT if timed_out else FailureKind.UNKNOWN,
                final_text=live.output_text,
            )
        if result is None:
            snapshot = self._make_snapshot(
                live,
                ExecutionState.FAILED,
                exit_code,
                result_complete=False,
                error={
                    "code": "NO_RESULT_OBJECT",
                    "message": "claude produced no JSON result object",
                },
                diagnostics=diagnostics,
            )
            return ExecutionResult(
                snapshot=snapshot,
                failure_kind=FailureKind.PROTOCOL_ERROR,
                final_text=live.output_text,
            )
        if result.get("is_error") or result.get("subtype") != "success":
            message = str(result.get("result") or result.get("subtype") or "claude reported an error")
            snapshot = self._make_snapshot(
                live,
                ExecutionState.FAILED,
                exit_code,
                result_complete=True,
                error={"code": "AGENT_RUNTIME_ERROR", "message": message},
                diagnostics=diagnostics,
            )
            return ExecutionResult(
                snapshot=snapshot,
                failure_kind=classify_runtime_error(message),
                final_text=live.output_text,
            )
        snapshot = self._make_snapshot(
            live,
            ExecutionState.FINISHED,
            exit_code,
            result_complete=True,
            diagnostics=diagnostics,
        )
        return ExecutionResult(
            snapshot=snapshot,
            failure_kind=FailureKind.NONE,
            final_text=live.output_text,
        )
