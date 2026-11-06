"""Validated snapshots and results, not business state transitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from collections.abc import Mapping
from typing import Any, TypeAlias

from ..errors import ContextFlowError
from ..submission import MAX_ID_LENGTH, MAX_TEXT_LENGTH
from ._validation import (
    freeze_mapping,
    invalid,
    require_text,
    require_tuple,
    require_type,
    require_utc,
    thaw_json,
)
from .envelopes import AgentReportEnvelope, SupervisorEnvelope
from .ids import AgentId, DeliveryId, EndpointId, MessageId, NativeSessionId, TaskId


class MessageKind(str, Enum):
    SUPERVISOR_INSTRUCTION = "supervisor_instruction"
    AGENT_REPORT = "agent_report"


class ProcessingState(str, Enum):
    PENDING = "pending"
    PROCESSED = "processed"
    BLOCKED = "blocked"


class ParseStatus(str, Enum):
    PARSED = "parsed"
    UNRESOLVED = "unresolved"
    FAILED = "failed"


class TaskState(str, Enum):
    PENDING_DELIVERY = "pending_delivery"
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    FAILED = "failed"


class DeliveryPurpose(str, Enum):
    TASK_DISPATCH = "task_dispatch"
    REPORT_RETURN = "report_return"


class DeliveryState(str, Enum):
    PENDING = "pending"
    SENDING = "sending"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class AcceptStatus(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CONFLICT = "conflict"


class CreateTasksStatus(str, Enum):
    CREATED = "created"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    CONFLICT = "conflict"


@dataclass(frozen=True, kw_only=True)
class ErrorInfo:
    """Immutable representation of the existing ContextFlowError, not a new exception."""

    code: str
    message: str
    retryable: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_text(self.code, "error.code", MAX_ID_LENGTH)
        require_text(self.message, "error.message")
        if type(self.retryable) is not bool:
            raise invalid("error.retryable", "must be a boolean")
        object.__setattr__(self, "details", freeze_mapping(self.details, "error.details"))

    @classmethod
    def from_error(cls, error: ContextFlowError) -> "ErrorInfo":
        require_type(error, ContextFlowError, "error")
        return cls(**error.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": thaw_json(self.details),
        }


def _require_error(error: ErrorInfo | None, field_name: str = "error") -> None:
    if error is not None:
        require_type(error, ErrorInfo, field_name)


def _require_processing(state: ProcessingState, error: ErrorInfo | None) -> None:
    require_type(state, ProcessingState, "processing_state")
    _require_error(error, "processing_error")
    if state is ProcessingState.BLOCKED and error is None:
        raise invalid("processing_error", "blocked work requires an error")
    if state is ProcessingState.PROCESSED and error is not None:
        raise invalid("processing_error", "processed work cannot retain an error")


@dataclass(frozen=True, kw_only=True)
class Message:
    message_id: MessageId
    kind: MessageKind
    envelope: SupervisorEnvelope | AgentReportEnvelope
    received_at: str
    source_agent_id: AgentId | None = None
    processing_state: ProcessingState = ProcessingState.PENDING
    processing_error: ErrorInfo | None = None

    def __post_init__(self) -> None:
        require_type(self.message_id, MessageId, "message_id")
        require_type(self.kind, MessageKind, "kind")
        require_utc(self.received_at, "received_at")
        _require_processing(self.processing_state, self.processing_error)
        if self.kind is MessageKind.SUPERVISOR_INSTRUCTION:
            require_type(self.envelope, SupervisorEnvelope, "envelope")
            if self.source_agent_id is not None:
                raise invalid("source_agent_id", "Supervisor messages have no Agent source")
        else:
            require_type(self.envelope, AgentReportEnvelope, "envelope")
            require_type(self.source_agent_id, AgentId, "source_agent_id")


@dataclass(frozen=True, kw_only=True)
class SupervisorSubmission:
    """Raw ingress. Acceptance requires validation by Message Store."""

    envelope: object


@dataclass(frozen=True, kw_only=True)
class AgentSubmission:
    """Internal Store input; source authenticity and Task association are D's responsibility."""

    source_agent_id: AgentId
    envelope: AgentReportEnvelope

    def __post_init__(self) -> None:
        require_type(self.source_agent_id, AgentId, "source_agent_id")
        require_type(self.envelope, AgentReportEnvelope, "envelope")


MessageSubmission: TypeAlias = SupervisorSubmission | AgentSubmission


@dataclass(frozen=True, kw_only=True)
class ParsedInstruction: #一条从监管者消息中提取出来的指令
    target_agent: str #指令明确指向的Agent名称
    content: str #交给这个Agent的任务正文

    def __post_init__(self) -> None: #进行校验
        require_text(self.target_agent, "target_agent", MAX_ID_LENGTH)
        require_text(self.content, "content", MAX_TEXT_LENGTH)
        for field_name in ("target_agent", "content"):
            try:
                str.encode(getattr(self, field_name), "utf-8")
            except UnicodeEncodeError as exc:
                raise invalid(field_name, "instruction text must be valid UTF-8 Unicode") from exc


@dataclass(frozen=True, kw_only=True)
class ParseResult: #一次解析的整体结果
    status: ParseStatus #解析状态
    instructions: tuple[ParsedInstruction, ...] = () #解析出的指令
    error: ErrorInfo | None = None #解析失败时的错误原因

    def __post_init__(self) -> None:
        require_type(self.status, ParseStatus, "status")
        require_tuple(self.instructions, ParsedInstruction, "instructions")
        _require_error(self.error)
        if self.status is ParseStatus.PARSED:
            if not self.instructions or self.error is not None:
                raise invalid("instructions", "parsed requires nonempty instructions and no error")
        elif self.instructions or self.error is None:
            raise invalid("instructions", "unresolved/failed requires an error and no instructions")

    @property
    def intent_fingerprint(self) -> str | None:
        """Candidate signature, not a parse version or a persistence operation.

        C must persist this signature atomically with a successful Task batch.
        The source Message ID supplies its namespace. Before commit, a new parse
        may differ; after commit, a different signature is TASK_BATCH_CONFLICT.
        """
        if self.status is not ParseStatus.PARSED:
            return None
        payload = [{"target_agent": item.target_agent, "content": item.content} for item in self.instructions]
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, kw_only=True)
class Task:
    task_id: TaskId
    source_message_id: MessageId
    instruction_index: int
    target_agent_id: AgentId
    content: str
    state: TaskState
    created_at: str
    updated_at: str
    final_report_message_id: MessageId | None = None

    def __post_init__(self) -> None:
        require_type(self.task_id, TaskId, "task_id")
        require_type(self.source_message_id, MessageId, "source_message_id")
        require_type(self.target_agent_id, AgentId, "target_agent_id")
        if type(self.instruction_index) is not int or self.instruction_index < 0:
            raise invalid("instruction_index", "must be a nonnegative integer")
        require_text(self.content, "content", MAX_TEXT_LENGTH)
        require_type(self.state, TaskState, "state")
        require_utc(self.created_at, "created_at")
        require_utc(self.updated_at, "updated_at")
        if self.state in (TaskState.COMPLETED, TaskState.FAILED):
            require_type(self.final_report_message_id, MessageId, "final_report_message_id")
        elif self.final_report_message_id is not None:
            raise invalid("final_report_message_id", "non-terminal tasks have no final report")


@dataclass(frozen=True, kw_only=True)
class _DeliveryFields:
    delivery_id: DeliveryId
    state: DeliveryState
    created_at: str
    updated_at: str
    error: ErrorInfo | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_type(self.delivery_id, DeliveryId, "delivery_id")
        require_type(self.state, DeliveryState, "state")
        require_utc(self.created_at, "created_at")
        require_utc(self.updated_at, "updated_at")
        _require_error(self.error)
        object.__setattr__(self, "evidence", freeze_mapping(self.evidence, "evidence"))
        if self.state in (DeliveryState.FAILED, DeliveryState.UNKNOWN) and self.error is None:
            raise invalid("error", "failed/unknown delivery requires an error")
        if self.state in (DeliveryState.SENDING, DeliveryState.CONFIRMED) and self.error is not None:
            raise invalid("error", "sending/confirmed delivery cannot contain an error")
        if self.state is DeliveryState.CONFIRMED and not self.evidence:
            raise invalid("evidence", "confirmed delivery requires receipt evidence")

    @property
    def can_attempt_send(self) -> bool:
        """State-level eligibility only; batch admission/availability still apply.

        ErrorInfo.retryable never overrides this gate. It does not perform a
        retry or turn an unknown outcome into a pending delivery.
        """
        return self.state is DeliveryState.PENDING


@dataclass(frozen=True, kw_only=True)
class TaskDelivery(_DeliveryFields):
    subject_id: TaskId
    target_id: AgentId
    purpose: DeliveryPurpose = field(default=DeliveryPurpose.TASK_DISPATCH, init=False)

    def __post_init__(self) -> None:
        super().__post_init__()
        require_type(self.subject_id, TaskId, "subject_id")
        require_type(self.target_id, AgentId, "target_id")


@dataclass(frozen=True, kw_only=True)
class ReportDelivery(_DeliveryFields):
    subject_id: MessageId
    target_id: EndpointId
    purpose: DeliveryPurpose = field(default=DeliveryPurpose.REPORT_RETURN, init=False)

    def __post_init__(self) -> None:
        super().__post_init__()
        require_type(self.subject_id, MessageId, "subject_id")
        require_type(self.target_id, EndpointId, "target_id")


Delivery: TypeAlias = TaskDelivery | ReportDelivery


@dataclass(frozen=True, kw_only=True)
class AcceptResult:
    status: AcceptStatus
    message_id: MessageId | None = None
    duplicate: bool = False
    error: ErrorInfo | None = None

    def __post_init__(self) -> None:
        require_type(self.status, AcceptStatus, "status")
        if type(self.duplicate) is not bool:
            raise invalid("duplicate", "must be a boolean")
        _require_error(self.error)
        if self.status is AcceptStatus.ACCEPTED:
            require_type(self.message_id, MessageId, "message_id")
            if self.error is not None:
                raise invalid("error", "accepted cannot contain an error")
        elif self.message_id is not None or self.duplicate or self.error is None:
            raise invalid("status", "rejected/conflict requires an error, no Message ID, and duplicate=false")


@dataclass(frozen=True, kw_only=True)
class CreateTasksResult:
    status: CreateTasksStatus
    tasks: tuple[Task, ...] = ()
    deliveries: tuple[TaskDelivery, ...] = ()
    intent_fingerprint: str | None = None
    error: ErrorInfo | None = None

    def __post_init__(self) -> None:
        require_type(self.status, CreateTasksStatus, "status")
        require_tuple(self.tasks, Task, "tasks")
        require_tuple(self.deliveries, TaskDelivery, "deliveries")
        _require_error(self.error)
        if not self.is_committed:
            if self.tasks or self.deliveries or self.intent_fingerprint is not None or self.error is None:
                raise invalid("status", "rejected/conflict cannot include a partial Task batch")
            return
        if self.error is not None or not self.tasks or len(self.tasks) != len(self.deliveries):
            raise invalid("tasks", "committed batch requires all Tasks and initial Deliveries, without error")
        fingerprint = self.intent_fingerprint
        if not isinstance(fingerprint, str) or len(fingerprint) != 64 or any(ch not in "0123456789abcdef" for ch in fingerprint):
            raise invalid("intent_fingerprint", "committed batch requires a SHA-256 candidate signature")
        if [item.instruction_index for item in self.tasks] != list(range(len(self.tasks))):
            raise invalid("instruction_index", "batch instructions must be ordered and contiguous from zero")
        if len({item.source_message_id for item in self.tasks}) != 1:
            raise invalid("source_message_id", "all Tasks must share one source Message")
        task_ids = {item.task_id for item in self.tasks}
        if len(task_ids) != len(self.tasks):
            raise invalid("task_id", "Task IDs must be unique within the batch")
        if len({item.delivery_id for item in self.deliveries}) != len(self.deliveries):
            raise invalid("delivery_id", "Delivery IDs must be unique within the batch")
        if {item.subject_id for item in self.deliveries} != task_ids:
            raise invalid("subject_id", "each Task requires exactly one initial Delivery")
        targets = {item.task_id: item.target_agent_id for item in self.tasks}
        if any(item.target_id != targets[item.subject_id] for item in self.deliveries):
            raise invalid("target_id", "Delivery target must match the Task target")
        if self.status is CreateTasksStatus.CREATED:
            if any(item.state is not TaskState.PENDING_DELIVERY for item in self.tasks):
                raise invalid("state", "new Tasks must be pending_delivery")
            if any(item.state is not DeliveryState.PENDING for item in self.deliveries):
                raise invalid("state", "new Deliveries must be pending")

    @property
    def is_committed(self) -> bool:
        """Contract promise from C; the record itself does not commit a database."""
        return self.status in (CreateTasksStatus.CREATED, CreateTasksStatus.DUPLICATE)


@dataclass(frozen=True, kw_only=True)
class ProcessResult:
    message_id: MessageId
    state: ProcessingState
    error: ErrorInfo | None = None

    def __post_init__(self) -> None:
        require_type(self.message_id, MessageId, "message_id")
        _require_processing(self.state, self.error)
        if self.state is ProcessingState.PENDING:
            raise invalid("state", "a processing result must be processed or blocked")


@dataclass(frozen=True, kw_only=True)
class DeliveryOutcome:
    state: DeliveryState
    error: ErrorInfo | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_type(self.state, DeliveryState, "state")
        _require_error(self.error)
        object.__setattr__(self, "evidence", freeze_mapping(self.evidence, "evidence"))
        if self.state is DeliveryState.SENDING:
            raise invalid("state", "Gateway must return an outcome, not an in-flight state")
        if self.state is DeliveryState.CONFIRMED:
            if self.error is not None or not self.evidence:
                raise invalid("evidence", "confirmed requires receipt evidence and no error")
        elif self.error is None:
            raise invalid("error", "pending/failed/unknown outcomes require a reason")

    @property
    def can_attempt_send(self) -> bool:
        """pending asserts that no external acceptance occurred; unknown cannot send."""
        return self.state is DeliveryState.PENDING


@dataclass(frozen=True, kw_only=True)
class EndpointIdentity:
    endpoint_id: EndpointId
    provider: str
    native_session_id: NativeSessionId

    def __post_init__(self) -> None:
        require_type(self.endpoint_id, EndpointId, "endpoint_id")
        require_text(self.provider, "provider", MAX_ID_LENGTH)
        require_type(self.native_session_id, NativeSessionId, "native_session_id")


@dataclass(frozen=True, kw_only=True)
class AgentDescriptor:
    agent_id: AgentId
    available: bool

    def __post_init__(self) -> None:
        require_type(self.agent_id, AgentId, "agent_id")
        if type(self.available) is not bool:
            raise invalid("available", "must be a boolean")
