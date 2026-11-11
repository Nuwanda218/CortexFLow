"""Formal Agent Runtime contracts.

Vocabulary (kept deliberately separate):

* **Task**      — logical work requested by the Supervisor. Core-owned.
* **Delivery**  — one attempt to hand a Task to an Agent. Core-owned.
* **Execution** — one concrete run on one Agent runtime. Adapter-owned identity,
                  Core-persisted record.
* **Session**   — the Agent's own conversation identity. Native, opaque string.

An Execution finishing says nothing about whether the Task goal was met. The
adapter reports execution facts only; acceptance stays with Core/Supervisor.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .errors import InvalidRequest

MAX_ID_LENGTH = 256
MAX_INSTRUCTION_LENGTH = 2_000_000
MAX_TIMEOUT_S = 86_400.0


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ExecutionState(str, Enum):
    """Execution lifecycle only; never business acceptance."""

    PENDING = "pending"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


TERMINAL_STATES = frozenset(
    {
        ExecutionState.FINISHED,
        ExecutionState.FAILED,
        ExecutionState.CANCELLED,
        ExecutionState.UNKNOWN,
    }
)


def is_terminal(state: ExecutionState) -> bool:
    return state in TERMINAL_STATES


class FailureKind(str, Enum):
    """Execution-level failure classification. Not a business verdict."""

    NONE = "none"
    RUNTIME_ERROR = "runtime_error"
    PROTOCOL_ERROR = "protocol_error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"
    CONCURRENCY_LIMIT = "concurrency_limit"
    AUTHENTICATION = "authentication"
    QUOTA_EXCEEDED = "quota_exceeded"
    SESSION_UNAVAILABLE = "session_unavailable"
    INVALID_REQUEST = "invalid_request"


class SessionPolicy(str, Enum):
    NEW = "new"       # always create a new native session
    REUSE = "reuse"   # require and reuse native_session_id
    AUTO = "auto"     # reuse when native_session_id is given, else create


class CancelDisposition(str, Enum):
    """What a cancel actually achieved. Cancelling never rolls back effects."""

    REQUEST_SENT = "request_sent"
    LOCAL_PROCESS_TERMINATED = "local_process_terminated"
    REMOTE_CONFIRMED_STOPPED = "remote_confirmed_stopped"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


class ControlKind(str, Enum):
    PROCESS = "process"
    SERVICE = "service"
    GUI = "gui"
    NONE = "none"


class EvidenceStatus(str, Enum):
    VERIFIED = "verified"
    PARTIAL = "partial"
    UNVERIFIED = "unverified"
    BLOCKED = "blocked"
    UNSUPPORTED = "unsupported"


# ---------------------------------------------------------------------------
# Validation helpers (adapter-local; no Core business dependency)
# ---------------------------------------------------------------------------


def _invalid(field_name: str, reason: str) -> InvalidRequest:
    return InvalidRequest(
        f"invalid execution request field {field_name}: {reason}",
        details={"field": field_name, "reason": reason},
    )


def _require_text(value: object, field_name: str, max_length: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(field_name, "must be a non-empty string")
    if max_length is not None and len(value) > max_length:
        raise _invalid(field_name, f"must be at most {max_length} characters")
    return str.__str__(value)


def _require_utc(value: object, field_name: str) -> str:
    text = _require_text(value, field_name)
    try:
        stamp = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise _invalid(field_name, "must be an ISO timestamp with UTC timezone") from exc
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise _invalid(field_name, "must be an ISO timestamp with UTC timezone")
    return text


def _freeze_json(value: Any, field_name: str) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, str):
        return str.__str__(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise _invalid(field_name, "non-finite numbers are not JSON values")
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise _invalid(field_name, "JSON object keys must be strings")
        return MappingProxyType({str.__str__(k): _freeze_json(v, field_name) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, field_name) for item in value)
    raise _invalid(field_name, "must contain only JSON values")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentCapabilities:
    """Declared capabilities. A False/absent capability must never fake success."""

    runtime: str
    control_kind: ControlKind
    supports_new_session: bool
    supports_session_reuse: bool
    supports_resume: bool
    supports_streaming: bool
    supports_structured_result: bool
    supports_structured_failure: bool
    supports_native_cancel: bool
    supports_usage: bool
    supports_permission_profiles: bool
    supports_workspace_isolation: bool
    supported_permission_profiles: tuple[str, ...] = ()
    max_concurrency: int | None = None
    evidence_status: EvidenceStatus = EvidenceStatus.UNVERIFIED
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.runtime, "capabilities.runtime")
        if not isinstance(self.control_kind, ControlKind):
            raise _invalid("capabilities.control_kind", "must be a ControlKind")
        if not isinstance(self.evidence_status, EvidenceStatus):
            raise _invalid("capabilities.evidence_status", "must be an EvidenceStatus")
        if self.max_concurrency is not None and (
            type(self.max_concurrency) is not int or self.max_concurrency < 1
        ):
            raise _invalid("capabilities.max_concurrency", "must be a positive integer or None")
        for profile in self.supported_permission_profiles:
            _require_text(profile, "capabilities.supported_permission_profiles")
        if self.supports_permission_profiles and not self.supported_permission_profiles:
            raise _invalid(
                "capabilities.supported_permission_profiles",
                "must be non-empty when permission profiles are supported",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime": self.runtime,
            "control_kind": self.control_kind.value,
            "supports_new_session": self.supports_new_session,
            "supports_session_reuse": self.supports_session_reuse,
            "supports_resume": self.supports_resume,
            "supports_streaming": self.supports_streaming,
            "supports_structured_result": self.supports_structured_result,
            "supports_structured_failure": self.supports_structured_failure,
            "supports_native_cancel": self.supports_native_cancel,
            "supports_usage": self.supports_usage,
            "supports_permission_profiles": self.supports_permission_profiles,
            "supports_workspace_isolation": self.supports_workspace_isolation,
            "supported_permission_profiles": list(self.supported_permission_profiles),
            "max_concurrency": self.max_concurrency,
            "evidence_status": self.evidence_status.value,
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionRequest:
    """One adapter-level execution request.

    ``caller_ref`` is an opaque correlation id supplied by the caller (the
    future Dispatcher would pass a Delivery id). It is *not* a TaskId and never
    replaces the native session id.
    """

    runtime: str
    instruction: str
    workdir: str
    caller_ref: str
    session_policy: SessionPolicy = SessionPolicy.AUTO
    native_session_id: str | None = None
    timeout_s: float = 300.0
    permission_profile: str = "workspace_write"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.runtime, "runtime", MAX_ID_LENGTH)
        _require_text(self.instruction, "instruction", MAX_INSTRUCTION_LENGTH)
        _require_text(self.caller_ref, "caller_ref", MAX_ID_LENGTH)
        _require_text(self.permission_profile, "permission_profile", MAX_ID_LENGTH)
        if not isinstance(self.session_policy, SessionPolicy):
            raise _invalid("session_policy", "must be a SessionPolicy")
        if self.native_session_id is not None:
            _require_text(self.native_session_id, "native_session_id", MAX_ID_LENGTH)
        if self.session_policy is SessionPolicy.REUSE and self.native_session_id is None:
            raise _invalid("native_session_id", "reuse policy requires a native session id")
        if self.session_policy is SessionPolicy.NEW and self.native_session_id is not None:
            raise _invalid("native_session_id", "new policy cannot carry a native session id")
        if type(self.timeout_s) not in (int, float) or not (0 < float(self.timeout_s) <= MAX_TIMEOUT_S):
            raise _invalid("timeout_s", f"must be in (0, {MAX_TIMEOUT_S:g}]")
        workdir = Path(self.workdir)
        if not workdir.is_absolute():
            raise _invalid("workdir", "must be an absolute path")
        object.__setattr__(self, "workdir", str(workdir))
        object.__setattr__(self, "metadata", _freeze_json(self.metadata, "metadata"))

    def fingerprint(self) -> str:
        """Stable identity of the requested work for local idempotency checks.

        Timeout and metadata are excluded: they change how an execution is run,
        not what was asked. Caller ref is the namespace, so it is excluded too.
        """
        payload = {
            "runtime": self.runtime,
            "instruction": self.instruction,
            "workdir": os.path.normcase(str(Path(self.workdir).resolve())),
            "session_policy": self.session_policy.value,
            "native_session_id": self.native_session_id,
            "permission_profile": self.permission_profile,
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime": self.runtime,
            "instruction": self.instruction,
            "workdir": self.workdir,
            "caller_ref": self.caller_ref,
            "session_policy": self.session_policy.value,
            "native_session_id": self.native_session_id,
            "timeout_s": self.timeout_s,
            "permission_profile": self.permission_profile,
            "metadata": _thaw(self.metadata),
        }


# ---------------------------------------------------------------------------
# Handle / snapshot / result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionHandle:
    """Identity of one execution, returned by ``start``.

    ``execution_id`` is Adapter-generated and persistable. ``native_session_id``
    is Agent-returned (or caller-supplied) and persistable. Having a session id
    does **not** imply that arbitrary past execution state can be recovered.
    """

    execution_id: str
    runtime: str
    caller_ref: str
    state: ExecutionState
    created_at: str
    native_session_id: str | None = None
    session_policy: SessionPolicy = SessionPolicy.AUTO
    resumable: bool = False
    pid: int | None = None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.execution_id, "execution_id", MAX_ID_LENGTH)
        _require_text(self.runtime, "runtime", MAX_ID_LENGTH)
        _require_text(self.caller_ref, "caller_ref", MAX_ID_LENGTH)
        if not isinstance(self.state, ExecutionState):
            raise _invalid("state", "must be an ExecutionState")
        _require_utc(self.created_at, "created_at")
        if self.native_session_id is not None:
            _require_text(self.native_session_id, "native_session_id", MAX_ID_LENGTH)
        if self.pid is not None and (type(self.pid) is not int or self.pid <= 0):
            raise _invalid("pid", "must be a positive integer or None")
        object.__setattr__(self, "diagnostics", _freeze_json(self.diagnostics, "diagnostics"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "runtime": self.runtime,
            "caller_ref": self.caller_ref,
            "state": self.state.value,
            "created_at": self.created_at,
            "native_session_id": self.native_session_id,
            "session_policy": self.session_policy.value,
            "resumable": self.resumable,
            "pid": self.pid,
            "diagnostics": _thaw(self.diagnostics),
        }


@dataclass(frozen=True)
class ExecutionSnapshot:
    """Point-in-time execution state. Never a business verdict."""

    execution_id: str
    runtime: str
    caller_ref: str
    state: ExecutionState
    native_state: str | None = None
    native_session_id: str | None = None
    exit_code: int | None = None
    output_text: str | None = None
    error: Mapping[str, Any] | None = None
    started_at: str | None = None
    ended_at: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    result_complete: bool = False
    side_effects_possible: bool = True
    retry_safe: bool = False
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.execution_id, "execution_id", MAX_ID_LENGTH)
        _require_text(self.runtime, "runtime", MAX_ID_LENGTH)
        _require_text(self.caller_ref, "caller_ref", MAX_ID_LENGTH)
        if not isinstance(self.state, ExecutionState):
            raise _invalid("state", "must be an ExecutionState")
        if self.native_state is not None:
            _require_text(self.native_state, "native_state", MAX_ID_LENGTH)
        if self.native_session_id is not None:
            _require_text(self.native_session_id, "native_session_id", MAX_ID_LENGTH)
        if self.exit_code is not None and type(self.exit_code) is not int:
            raise _invalid("exit_code", "must be an integer or None")
        if self.started_at is not None:
            _require_utc(self.started_at, "started_at")
        if self.ended_at is not None:
            _require_utc(self.ended_at, "ended_at")
        for name in ("usage", "diagnostics"):
            object.__setattr__(self, name, _freeze_json(getattr(self, name), name))
        if self.error is not None:
            if not isinstance(self.error, Mapping):
                raise _invalid("error", "must be a mapping or None")
            object.__setattr__(self, "error", _freeze_json(self.error, "error"))
        if type(self.result_complete) is not bool:
            raise _invalid("result_complete", "must be a boolean")
        if type(self.side_effects_possible) is not bool:
            raise _invalid("side_effects_possible", "must be a boolean")
        if type(self.retry_safe) is not bool:
            raise _invalid("retry_safe", "must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "runtime": self.runtime,
            "caller_ref": self.caller_ref,
            "state": self.state.value,
            "native_state": self.native_state,
            "native_session_id": self.native_session_id,
            "exit_code": self.exit_code,
            "output_text": self.output_text,
            "error": _thaw(self.error),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "usage": _thaw(self.usage),
            "result_complete": self.result_complete,
            "side_effects_possible": self.side_effects_possible,
            "retry_safe": self.retry_safe,
            "diagnostics": _thaw(self.diagnostics),
        }


@dataclass(frozen=True)
class CancelResult:
    """What a cancel request actually achieved.

    Cancelling never rolls back file changes or other side effects.
    """

    execution_id: str
    disposition: CancelDisposition
    requested_at: str
    snapshot: ExecutionSnapshot
    confirmed_at: str | None = None
    note: str = ""

    def __post_init__(self) -> None:
        _require_text(self.execution_id, "execution_id", MAX_ID_LENGTH)
        if not isinstance(self.disposition, CancelDisposition):
            raise _invalid("disposition", "must be a CancelDisposition")
        _require_utc(self.requested_at, "requested_at")
        if not isinstance(self.snapshot, ExecutionSnapshot):
            raise _invalid("snapshot", "must be an ExecutionSnapshot")
        if self.confirmed_at is not None:
            _require_utc(self.confirmed_at, "confirmed_at")
        if not isinstance(self.note, str):
            raise _invalid("note", "must be a string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "disposition": self.disposition.value,
            "requested_at": self.requested_at,
            "confirmed_at": self.confirmed_at,
            "note": self.note,
            "snapshot": self.snapshot.to_dict(),
        }


@dataclass(frozen=True)
class ExecutionResult:
    """Terminal (or best-known) result of one execution.

    ``failure_kind`` classifies the execution, not the business outcome.
    ``final_text`` is the Agent's own final message, if any.
    """

    snapshot: ExecutionSnapshot
    failure_kind: FailureKind = FailureKind.NONE
    final_text: str | None = None
    cancel: CancelResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, ExecutionSnapshot):
            raise _invalid("snapshot", "must be an ExecutionSnapshot")
        if not isinstance(self.failure_kind, FailureKind):
            raise _invalid("failure_kind", "must be a FailureKind")
        if self.final_text is not None and not isinstance(self.final_text, str):
            raise _invalid("final_text", "must be a string or None")
        if self.cancel is not None and not isinstance(self.cancel, CancelResult):
            raise _invalid("cancel", "must be a CancelResult or None")

    @property
    def state(self) -> ExecutionState:
        return self.snapshot.state

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot": self.snapshot.to_dict(),
            "failure_kind": self.failure_kind.value,
            "final_text": self.final_text,
            "cancel": self.cancel.to_dict() if self.cancel is not None else None,
        }
