"""Base classes for Agent Runtime adapters.

Two layers:

* ``AgentRuntimeAdapter`` — the formal async interface the future Dispatcher
  calls: capabilities / start / inspect / cancel (+ wait/result helpers).
* ``ProcessRuntimeAdapter`` — shared implementation for CLI runtimes: one child
  process per execution, JSONL/JSON stdout parsing, process-tree cancellation,
  local idempotency journal and conservative unknown handling.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import (
    AgentCapabilities,
    CancelDisposition,
    CancelResult,
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
from .errors import (
    ConcurrencyLimit,
    ExecutionNotFound,
    IdempotencyConflict,
    InvalidRequest,
    UnsupportedOperation,
)
from .journal import ExecutionJournal, JournalRecord


def _iso_from_epoch(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class ConcurrencyGuard:
    """Non-blocking per-runtime concurrency limit.

    A limit is a hard constraint (e.g. Codex-Relay allows one conversation at a
    time), so exceeding it raises ``ConcurrencyLimit`` instead of queuing
    silently. Core decides whether to retry later.
    """

    def __init__(self, limit: int | None) -> None:
        self.limit = limit
        self._lock = threading.Lock()
        self._active = 0

    def acquire(self) -> None:
        if self.limit is None:
            return
        with self._lock:
            if self._active >= self.limit:
                raise ConcurrencyLimit(
                    f"runtime allows at most {self.limit} concurrent execution(s)",
                    retryable=True,
                    details={"limit": self.limit, "active": self._active},
                )
            self._active += 1

    def release(self) -> None:
        if self.limit is None:
            return
        with self._lock:
            if self._active > 0:
                self._active -= 1

    @property
    def active(self) -> int:
        return self._active


class AgentRuntimeAdapter(ABC):
    """Formal adapter interface. Async because Core is asyncio-based."""

    runtime: str
    capabilities: AgentCapabilities

    @abstractmethod
    async def probe(self) -> dict[str, Any]:
        """Environment/version/availability diagnostics. Must not execute work."""

    @abstractmethod
    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        """Accept one execution and return its handle. Does not wait for finish."""

    @abstractmethod
    async def inspect(self, execution_id: str) -> ExecutionSnapshot:
        """Current execution state. Raises ExecutionNotFound for unknown ids."""

    @abstractmethod
    async def cancel(self, execution_id: str) -> CancelResult:
        """Request cancellation and report what was actually achieved."""

    async def wait(self, execution_id: str, timeout_s: float | None = None) -> ExecutionResult:
        """Wait for a terminal state, then return the result.

        On timeout the execution is cancelled and reported as UNKNOWN/TIMEOUT:
        a killed execution may already have produced side effects.
        """
        deadline = time.monotonic() + timeout_s if timeout_s is not None else None
        while True:
            snapshot = await self.inspect(execution_id)
            if is_terminal(snapshot.state):
                return await self.result(execution_id)
            if deadline is not None and time.monotonic() >= deadline:
                cancel_result = await self.cancel(execution_id)
                return ExecutionResult(
                    snapshot=cancel_result.snapshot,
                    failure_kind=FailureKind.TIMEOUT,
                    final_text=cancel_result.snapshot.output_text,
                    cancel=cancel_result,
                )
            await asyncio.sleep(0.2)

    async def result(self, execution_id: str) -> ExecutionResult:
        snapshot = await self.inspect(execution_id)
        failure = FailureKind.NONE
        if snapshot.state is ExecutionState.UNKNOWN:
            failure = FailureKind.UNKNOWN
        elif snapshot.state is ExecutionState.CANCELLED:
            failure = FailureKind.CANCELLED
        elif snapshot.state is ExecutionState.FAILED:
            failure = FailureKind.RUNTIME_ERROR
        return ExecutionResult(snapshot=snapshot, failure_kind=failure, final_text=snapshot.output_text)

    async def close(self) -> None:
        """Release adapter resources. Default: nothing to release."""


# ---------------------------------------------------------------------------
# Process adapter
# ---------------------------------------------------------------------------


@dataclass
class LiveExecution:
    execution_id: str
    proc: subprocess.Popen[str]
    handle: ExecutionHandle
    request: ExecutionRequest
    started_wall: float
    started_mono: float
    deadline_mono: float
    lines: list[dict[str, Any]] = field(default_factory=list)
    stderr: list[str] = field(default_factory=list)
    threads: list[threading.Thread] = field(default_factory=list)
    native_state: str | None = None
    native_session_id: str | None = None
    output_text: str | None = None
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    killed: bool = False
    timed_out: bool = False
    cancel_disposition: CancelDisposition | None = None
    result: ExecutionResult | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    finalize_lock: threading.Lock = field(default_factory=threading.Lock)


class ProcessRuntimeAdapter(AgentRuntimeAdapter):
    """Shared implementation for CLI runtimes."""

    def __init__(
        self,
        journal_dir: str | Path,
        command: list[str] | None = None,
        env: dict[str, str] | None = None,
        max_concurrency: int | None = None,
        poll_interval: float = 0.1,
    ) -> None:
        self.journal = ExecutionJournal(journal_dir)
        self.command = command or []
        self.env = dict(env or {})
        self.poll_interval = poll_interval
        self._guard = ConcurrencyGuard(max_concurrency)
        self._live: dict[str, LiveExecution] = {}
        self._results: dict[str, ExecutionResult] = {}
        self._lock = threading.RLock()

    # -- subclass hooks ---------------------------------------------------
    @abstractmethod
    def _build_argv(self, request: ExecutionRequest) -> list[str]:
        """Build the full argv (argv[0] may be a bare command name)."""

    def _pre_session_id(self, request: ExecutionRequest) -> str | None:
        """Session id known before launch (e.g. caller-chosen UUID)."""
        return request.native_session_id

    def _on_event(self, live: LiveExecution, event: dict[str, Any]) -> None:
        """Update live state from one parsed stdout event."""

    @abstractmethod
    def _finalize(
        self,
        live: LiveExecution,
        exit_code: int | None,
        killed: bool,
        timed_out: bool,
    ) -> ExecutionResult:
        """Map process exit + collected events to a terminal result."""

    def _request_cancel(self, live: LiveExecution) -> CancelDisposition:
        self._terminate_tree(live.proc)
        return CancelDisposition.LOCAL_PROCESS_TERMINATED

    # -- validation -------------------------------------------------------
    def _validate_request(self, request: ExecutionRequest) -> None:
        if request.runtime != self.runtime:
            raise InvalidRequest(
                f"request runtime {request.runtime!r} does not match adapter {self.runtime!r}",
                details={"request_runtime": request.runtime, "adapter_runtime": self.runtime},
            )
        if self.capabilities.supports_permission_profiles:
            if request.permission_profile not in self.capabilities.supported_permission_profiles:
                raise InvalidRequest(
                    f"unsupported permission profile {request.permission_profile!r}",
                    details={
                        "permission_profile": request.permission_profile,
                        "supported": list(self.capabilities.supported_permission_profiles),
                    },
                )
        if request.session_policy is SessionPolicy.NEW and not self.capabilities.supports_new_session:
            raise UnsupportedOperation(f"{self.runtime} cannot create new sessions")
        if request.session_policy is SessionPolicy.REUSE and not self.capabilities.supports_session_reuse:
            raise UnsupportedOperation(f"{self.runtime} cannot reuse sessions")

    # -- interface --------------------------------------------------------
    async def probe(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._probe_sync)

    def _probe_sync(self) -> dict[str, Any]:
        exe = self.command[0] if self.command else None
        resolved = shutil.which(exe) if exe else None
        version = None
        if resolved:
            try:
                out = subprocess.run(
                    [resolved, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=self._env(),
                )
                text = (out.stdout or out.stderr).strip()
                version = text.splitlines()[0] if text else None
            except (OSError, subprocess.SubprocessError, IndexError):
                version = None
        return {
            "runtime": self.runtime,
            "command": self.command,
            "resolved": resolved,
            "version": version,
            "capabilities": self.capabilities.to_dict(),
        }

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(self.env)
        return env

    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        self._validate_request(request)
        return await asyncio.to_thread(self._start_sync, request)

    def _start_sync(self, request: ExecutionRequest) -> ExecutionHandle:
        existing = self.journal.lookup(request.caller_ref, request.fingerprint())
        if existing is not None:
            if existing.state is ExecutionState.UNKNOWN:
                raise IdempotencyConflict(
                    "an earlier execution for this caller_ref has unknown outcome; "
                    "reconcile it instead of re-executing",
                    details={
                        "caller_ref": request.caller_ref,
                        "execution_id": existing.execution_id,
                    },
                )
            return self._handle_from_record(existing)
        self._guard.acquire()
        try:
            argv = list(self._build_argv(request))
            resolved = shutil.which(argv[0])
            if resolved:
                argv[0] = resolved
            Path(request.workdir).mkdir(parents=True, exist_ok=True)
            proc = subprocess.Popen(
                argv,
                cwd=request.workdir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self._env(),
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0,
            )
        except OSError as exc:
            self._guard.release()
            hint = self.command[0] if self.command else "?"
            raise InvalidRequest(f"cannot start {hint!r}: {exc}") from exc
        except Exception:
            self._guard.release()
            raise
        execution_id = "cfexec_" + uuid.uuid4().hex
        native_session_id = self._pre_session_id(request)
        handle = ExecutionHandle(
            execution_id=execution_id,
            runtime=self.runtime,
            caller_ref=request.caller_ref,
            state=ExecutionState.RUNNING,
            created_at=utc_now(),
            native_session_id=native_session_id,
            session_policy=request.session_policy,
            resumable=self.capabilities.supports_resume,
            pid=proc.pid,
            diagnostics={"argv": argv},
        )
        live = LiveExecution(
            execution_id=execution_id,
            proc=proc,
            handle=handle,
            request=request,
            started_wall=time.time(),
            started_mono=time.monotonic(),
            deadline_mono=time.monotonic() + float(request.timeout_s),
            native_session_id=native_session_id,
        )
        live.threads = [
            threading.Thread(target=self._read_stdout, args=(live,), daemon=True),
            threading.Thread(target=self._read_stderr, args=(live,), daemon=True),
        ]
        for thread in live.threads:
            thread.start()
        with self._lock:
            self._live[execution_id] = live
        self.journal.register(
            JournalRecord(
                execution_id=execution_id,
                runtime=self.runtime,
                caller_ref=request.caller_ref,
                fingerprint=request.fingerprint(),
                state=ExecutionState.RUNNING,
                created_at=handle.created_at,
                updated_at=handle.created_at,
                native_session_id=native_session_id,
                pid=proc.pid,
                resumable=handle.resumable,
            )
        )
        return handle

    def _handle_from_record(self, record: JournalRecord) -> ExecutionHandle:
        return ExecutionHandle(
            execution_id=record.execution_id,
            runtime=record.runtime,
            caller_ref=record.caller_ref,
            state=record.state,
            created_at=record.created_at,
            native_session_id=record.native_session_id,
            session_policy=SessionPolicy.AUTO,
            resumable=record.resumable,
            pid=record.pid,
            diagnostics={"duplicate_suppressed": True, "journal_state": record.state.value},
        )

    async def inspect(self, execution_id: str) -> ExecutionSnapshot:
        return await asyncio.to_thread(self._inspect_sync, execution_id)

    def _inspect_sync(self, execution_id: str) -> ExecutionSnapshot:
        with self._lock:
            live = self._live.get(execution_id)
        if live is not None:
            self._finalize_if_exited(live)
            return self._snapshot(live)
        record = self.journal.get(execution_id)
        if record is None:
            raise ExecutionNotFound(
                f"unknown execution id {execution_id!r}",
                details={"execution_id": execution_id},
            )
        return self._snapshot_from_record(record)

    async def cancel(self, execution_id: str) -> CancelResult:
        return await asyncio.to_thread(self._cancel_sync, execution_id)

    def _cancel_sync(self, execution_id: str) -> CancelResult:
        with self._lock:
            live = self._live.get(execution_id)
        if live is None:
            record = self.journal.get(execution_id)
            if record is None:
                raise ExecutionNotFound(
                    f"unknown execution id {execution_id!r}",
                    details={"execution_id": execution_id},
                )
            return CancelResult(
                execution_id=execution_id,
                disposition=CancelDisposition.UNSUPPORTED,
                requested_at=utc_now(),
                snapshot=self._snapshot_from_record(record),
                note="execution is not live in this adapter process; nothing to cancel",
            )
        if live.result is not None:
            return CancelResult(
                execution_id=execution_id,
                disposition=CancelDisposition.UNSUPPORTED,
                requested_at=utc_now(),
                snapshot=live.result.snapshot,
                note="execution already reached a terminal state; cancel is a no-op",
            )
        disposition = self._request_cancel(live)
        live.killed = True
        try:
            live.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        result = self._finalize(live, live.proc.poll(), killed=True, timed_out=False)
        result = ExecutionResult(
            snapshot=result.snapshot,
            failure_kind=(
                FailureKind.CANCELLED
                if disposition is CancelDisposition.REMOTE_CONFIRMED_STOPPED
                else FailureKind.UNKNOWN
            ),
            final_text=result.final_text,
            cancel=None,
        )
        self._store_result(live, result)
        return CancelResult(
            execution_id=execution_id,
            disposition=disposition,
            requested_at=utc_now(),
            confirmed_at=utc_now() if disposition is CancelDisposition.REMOTE_CONFIRMED_STOPPED else None,
            snapshot=result.snapshot,
            note=(
                "local process tree terminated; remote side effects are not rolled back"
                if disposition is CancelDisposition.LOCAL_PROCESS_TERMINATED
                else "cancel outcome recorded"
            ),
        )

    async def wait(self, execution_id: str, timeout_s: float | None = None) -> ExecutionResult:
        if timeout_s is None:
            with self._lock:
                live = self._live.get(execution_id)
            deadline = live.deadline_mono if live is not None else None
        else:
            deadline = time.monotonic() + timeout_s
        while True:
            snapshot = await self.inspect(execution_id)
            if is_terminal(snapshot.state):
                return await self.result(execution_id)
            if deadline is not None and time.monotonic() >= deadline:
                cancel_result = await self.cancel(execution_id)
                return ExecutionResult(
                    snapshot=cancel_result.snapshot,
                    failure_kind=FailureKind.TIMEOUT,
                    final_text=cancel_result.snapshot.output_text,
                    cancel=cancel_result,
                )
            await asyncio.sleep(self.poll_interval)

    async def result(self, execution_id: str) -> ExecutionResult:
        return await asyncio.to_thread(self._result_sync, execution_id)

    def _result_sync(self, execution_id: str) -> ExecutionResult:
        with self._lock:
            cached = self._results.get(execution_id)
            live = self._live.get(execution_id)
        if cached is not None:
            return cached
        if live is not None:
            self._finalize_if_exited(live)
            with self._lock:
                cached = self._results.get(execution_id)
            if cached is not None:
                return cached
            snapshot = self._snapshot(live)
            return ExecutionResult(
                snapshot=snapshot,
                failure_kind=FailureKind.NONE,
                final_text=snapshot.output_text,
            )
        record = self.journal.get(execution_id)
        if record is None:
            raise ExecutionNotFound(
                f"unknown execution id {execution_id!r}",
                details={"execution_id": execution_id},
            )
        snapshot = self._snapshot_from_record(record)
        failure = FailureKind.UNKNOWN if snapshot.state is ExecutionState.UNKNOWN else FailureKind.NONE
        return ExecutionResult(snapshot=snapshot, failure_kind=failure, final_text=snapshot.output_text)

    async def close(self) -> None:
        await asyncio.to_thread(self._close_sync)

    def _close_sync(self) -> None:
        with self._lock:
            live_ids = list(self._live)
        for execution_id in live_ids:
            try:
                self._cancel_sync(execution_id)
            except Exception:
                pass

    # -- internals --------------------------------------------------------
    def _read_stdout(self, live: LiveExecution) -> None:
        if live.proc.stdout is None:
            return
        for line in live.proc.stdout:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                event = {"type": "raw", "line": line}
            with live.lock:
                live.lines.append(event)
                try:
                    self._on_event(live, event)
                except Exception:
                    pass

    def _read_stderr(self, live: LiveExecution) -> None:
        if live.proc.stderr is None:
            return
        for line in live.proc.stderr:
            with live.lock:
                live.stderr.append(line.rstrip("\n"))

    def _finalize_if_exited(self, live: LiveExecution) -> None:
        with live.finalize_lock:
            if live.result is not None:
                return
            if live.proc.poll() is None:
                return
            result = self._finalize(live, live.proc.poll(), killed=live.killed, timed_out=live.timed_out)
            self._store_result(live, result)

    def _store_result(self, live: LiveExecution, result: ExecutionResult) -> None:
        with self._lock:
            live.result = result
            self._results[live.execution_id] = result
        try:
            self.journal.update(
                live.execution_id,
                state=result.state,
                native_session_id=result.snapshot.native_session_id or live.native_session_id,
                note=result.failure_kind.value,
            )
        except KeyError:
            pass
        self._guard.release()

    def _snapshot(self, live: LiveExecution) -> ExecutionSnapshot:
        with live.lock:
            lines = len(live.lines)
            stderr_tail = list(live.stderr[-20:])
            native_state = live.native_state
            output_text = live.output_text
            usage = dict(live.usage)
            error = dict(live.error) if live.error else None
        exit_code = live.proc.poll()
        state = self._state_for(live, exit_code)
        ended = _iso_from_epoch(time.time()) if is_terminal(state) else None
        return ExecutionSnapshot(
            execution_id=live.execution_id,
            runtime=self.runtime,
            caller_ref=live.request.caller_ref,
            state=state,
            native_state=native_state,
            native_session_id=live.native_session_id,
            exit_code=exit_code,
            output_text=output_text,
            error=error,
            started_at=_iso_from_epoch(live.started_wall),
            ended_at=ended,
            usage=usage,
            result_complete=live.result is not None and live.result.snapshot.result_complete,
            side_effects_possible=True,
            retry_safe=False,
            diagnostics={
                "event_count": lines,
                "stderr_tail": stderr_tail,
                "argv": list(live.handle.diagnostics.get("argv", ())),
            },
        )

    def _state_for(self, live: LiveExecution, exit_code: int | None) -> ExecutionState:
        if live.result is not None:
            return live.result.state
        if exit_code is None:
            return ExecutionState.RUNNING
        # Process exited but the adapter has not finalized yet: report the
        # conservative state until finalize runs.
        return ExecutionState.UNKNOWN

    def _make_snapshot(
        self,
        live: LiveExecution,
        state: ExecutionState,
        exit_code: int | None,
        *,
        result_complete: bool,
        error: dict[str, Any] | None = None,
        native_state: str | None = None,
        usage: dict[str, Any] | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> ExecutionSnapshot:
        """Build a terminal/point-in-time snapshot for a live execution."""
        with live.lock:
            stderr_tail = list(live.stderr[-20:])
            tool_events = list(live.tool_events[-50:])
            output_text = live.output_text
            native_session_id = live.native_session_id
            native_state = native_state if native_state is not None else live.native_state
            usage = dict(usage if usage is not None else live.usage)
        ended = _iso_from_epoch(time.time()) if is_terminal(state) else None
        merged_diagnostics: dict[str, Any] = {
            "event_count": len(live.lines),
            "stderr_tail": stderr_tail,
            "tool_events": tool_events,
            "argv": list(live.handle.diagnostics.get("argv", ())),
        }
        if diagnostics:
            merged_diagnostics.update(diagnostics)
        return ExecutionSnapshot(
            execution_id=live.execution_id,
            runtime=self.runtime,
            caller_ref=live.request.caller_ref,
            state=state,
            native_state=native_state,
            native_session_id=native_session_id,
            exit_code=exit_code,
            output_text=output_text,
            error=error,
            started_at=_iso_from_epoch(live.started_wall),
            ended_at=ended,
            usage=usage,
            result_complete=result_complete,
            side_effects_possible=True,
            retry_safe=False,
            diagnostics=merged_diagnostics,
        )

    def _snapshot_from_record(self, record: JournalRecord) -> ExecutionSnapshot:
        return ExecutionSnapshot(
            execution_id=record.execution_id,
            runtime=record.runtime,
            caller_ref=record.caller_ref,
            state=record.state,
            native_session_id=record.native_session_id,
            started_at=record.created_at,
            ended_at=record.updated_at if is_terminal(record.state) else None,
            result_complete=False,
            side_effects_possible=True,
            retry_safe=False,
            diagnostics={
                "journal_only": True,
                "note": record.note,
                "pid": record.pid,
            },
        )

    @staticmethod
    def _terminate_tree(proc: subprocess.Popen[str]) -> None:
        if proc.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except OSError:
                proc.terminate()
