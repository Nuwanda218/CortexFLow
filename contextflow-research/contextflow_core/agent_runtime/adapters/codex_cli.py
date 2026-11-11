"""Codex CLI runtime adapter.

Verified against codex-cli 0.162.1 (C-02 evidence). Native mapping:

* ``thread.started.thread_id``      -> native session id (resumable)
* ``turn.started``                  -> running
* ``item.completed`` agent_message  -> output text
* ``item.completed`` command_execution -> tool event (exit code kept as evidence)
* ``turn.completed``                -> finished + usage
* ``turn.failed``                   -> failed (runtime/quota/auth/concurrency)
* process killed / no terminal event -> unknown (side effects possible)
"""

from __future__ import annotations

import hashlib
import json
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

SANDBOX_BY_PROFILE = {
    "read_only": "read-only",
    "workspace_write": "workspace-write",
    "full_access": "danger-full-access",
}


def classify_runtime_error(message: str) -> FailureKind:
    text = (message or "").lower()
    if "only one codex conversation" in text or ("403" in text and "conversation" in text):
        return FailureKind.CONCURRENCY_LIMIT
    if "usage limit" in text or "quota" in text or "rate limit" in text or "too many requests" in text:
        return FailureKind.QUOTA_EXCEEDED
    if "401" in text or "unauthorized" in text or "authentication" in text or "login" in text:
        return FailureKind.AUTHENTICATION
    return FailureKind.RUNTIME_ERROR


class CodexCliAdapter(ProcessRuntimeAdapter):
    runtime = "codex-cli"
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
        supported_permission_profiles=tuple(SANDBOX_BY_PROFILE),
        max_concurrency=None,
        evidence_status=EvidenceStatus.VERIFIED,
        notes=(
            "codex exec --json emits JSONL; -o writes the final agent message",
            "thread id is the resumable native session id",
            "foreground exec has no native cancel; kill reports unknown",
            "approval_policy is forced to never for non-interactive use",
            "user config/MCP/skills are loaded unless CODEX_HOME is isolated",
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
            command=command or ["codex"],
            env=env,
            max_concurrency=max_concurrency,
        )

    # -- argv -------------------------------------------------------------
    def _last_message_path(self, request: ExecutionRequest) -> Path:
        digest = hashlib.sha256(request.caller_ref.encode("utf-8")).hexdigest()[:16]
        return Path(request.workdir) / f".cf-last-message-{digest}.txt"

    def _build_argv(self, request: ExecutionRequest) -> list[str]:
        sandbox = SANDBOX_BY_PROFILE.get(request.permission_profile)
        if sandbox is None:
            raise InvalidRequest(
                f"unsupported permission profile {request.permission_profile!r}",
                details={"supported": list(SANDBOX_BY_PROFILE)},
            )
        argv = list(self.command)
        resume_id = self._resume_session_id(request)
        if resume_id:
            argv += ["exec", "resume", resume_id, "--json"]
        else:
            argv += ["exec", "--json", "-C", request.workdir, "-s", sandbox]
        argv += [
            "-c",
            'approval_policy="never"',
            "--skip-git-repo-check",
            "-o",
            str(self._last_message_path(request)),
            request.instruction,
        ]
        return argv

    @staticmethod
    def _resume_session_id(request: ExecutionRequest) -> str | None:
        if request.session_policy is SessionPolicy.REUSE:
            return request.native_session_id
        if request.session_policy is SessionPolicy.AUTO:
            return request.native_session_id
        return None

    def _pre_session_id(self, request: ExecutionRequest) -> str | None:
        return self._resume_session_id(request)

    # -- events -----------------------------------------------------------
    def _on_event(self, live: LiveExecution, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "thread.started":
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str) and thread_id:
                live.native_session_id = thread_id
            live.native_state = "thread_started"
        elif kind == "turn.started":
            live.native_state = "turn_started"
        elif kind == "turn.completed":
            live.native_state = "turn_completed"
            usage = event.get("usage")
            if isinstance(usage, dict):
                live.usage = dict(usage)
        elif kind == "turn.failed":
            live.native_state = "turn_failed"
            error = event.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else str(error)
            live.error = {"message": str(message or "turn failed")}
        elif kind == "item.completed":
            item = event.get("item") or {}
            item_type = item.get("type")
            if item_type == "agent_message":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    live.output_text = text
            elif item_type == "command_execution":
                live.tool_events.append(
                    {
                        "command": item.get("command"),
                        "exit_code": item.get("exit_code"),
                        "status": item.get("status"),
                    }
                )
            elif item_type == "file_change":
                live.tool_events.append(
                    {
                        "kind": "file_change",
                        "changes": item.get("changes"),
                        "status": item.get("status"),
                    }
                )

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
            output_text = live.output_text
        thread_id = next(
            (event.get("thread_id") for event in lines if event.get("type") == "thread.started"),
            None,
        )
        if thread_id:
            live.native_session_id = thread_id
        turn_completed = next(
            (event for event in lines if event.get("type") == "turn.completed"),
            None,
        )
        turn_failed = next(
            (event for event in lines if event.get("type") == "turn.failed"),
            None,
        )
        usage = dict(turn_completed.get("usage") or {}) if turn_completed else {}
        if not output_text:
            try:
                path = self._last_message_path(live.request)
                if path.exists():
                    output_text = path.read_text(encoding="utf-8", errors="replace").strip() or None
            except OSError:
                output_text = None
        diagnostics: dict[str, Any] = {
            "last_message_file": str(self._last_message_path(live.request)),
        }
        if killed:
            snapshot = self._make_snapshot(
                live,
                ExecutionState.UNKNOWN,
                exit_code,
                result_complete=False,
                error={
                    "code": "EXECUTION_OUTCOME_UNKNOWN",
                    "message": (
                        "adapter terminated the process tree; no terminal event was "
                        "observed, so external side effects cannot be ruled out"
                    ),
                },
                native_state="killed",
                usage=usage,
                diagnostics=diagnostics,
            )
            return ExecutionResult(
                snapshot=snapshot,
                failure_kind=FailureKind.TIMEOUT if timed_out else FailureKind.UNKNOWN,
                final_text=output_text,
            )
        if turn_failed is not None:
            error = turn_failed.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else str(error)
            message = str(message or "turn failed")
            snapshot = self._make_snapshot(
                live,
                ExecutionState.FAILED,
                exit_code,
                result_complete=True,
                error={"code": "AGENT_RUNTIME_ERROR", "message": message},
                native_state="turn_failed",
                usage=usage,
                diagnostics=diagnostics,
            )
            return ExecutionResult(
                snapshot=snapshot,
                failure_kind=classify_runtime_error(message),
                final_text=output_text,
            )
        if turn_completed is not None:
            snapshot = self._make_snapshot(
                live,
                ExecutionState.FINISHED,
                exit_code,
                result_complete=True,
                native_state="turn_completed",
                usage=usage,
                diagnostics=diagnostics,
            )
            return ExecutionResult(
                snapshot=snapshot,
                failure_kind=FailureKind.NONE,
                final_text=output_text,
            )
        if exit_code == 0:
            snapshot = self._make_snapshot(
                live,
                ExecutionState.UNKNOWN,
                exit_code,
                result_complete=False,
                error={
                    "code": "NO_TERMINAL_EVENT",
                    "message": "process exited 0 without turn.completed/turn.failed",
                },
                native_state=live.native_state,
                usage=usage,
                diagnostics=diagnostics,
            )
            return ExecutionResult(
                snapshot=snapshot,
                failure_kind=FailureKind.PROTOCOL_ERROR,
                final_text=output_text,
            )
        snapshot = self._make_snapshot(
            live,
            ExecutionState.FAILED,
            exit_code,
            result_complete=False,
            error={
                "code": "CLI_EXIT_NONZERO",
                "message": f"codex exited with {exit_code}",
            },
            native_state=live.native_state,
            usage=usage,
            diagnostics=diagnostics,
        )
        return ExecutionResult(
            snapshot=snapshot,
            failure_kind=FailureKind.RUNTIME_ERROR,
            final_text=output_text,
        )


def read_codex_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Read a recorded Codex JSONL stream (used by tests and diagnostics)."""
    events: list[dict[str, Any]] = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return events
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            events.append({"type": "raw", "line": line})
    return events
