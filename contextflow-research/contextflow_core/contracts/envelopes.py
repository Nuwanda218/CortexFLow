"""Immutable message envelopes; Supervisor wire v1 remains unchanged."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..submission import MAX_ID_LENGTH, MAX_TEXT_LENGTH, validate_envelope
from ._validation import exact_fields, invalid, require_text, require_type
from .ids import EndpointId, NativeMessageId, NativeSessionId, ReportId, SubmissionId, TaskId


class ReportKind(str, Enum):
    PROGRESS = "progress"
    QUESTION = "question"
    BLOCKED = "blocked"
    FINAL = "final"


class ReportOutcome(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, kw_only=True)
class Content:
    text: str
    format: str = "markdown"

    def __post_init__(self) -> None:
        require_text(self.text, "content.text", MAX_TEXT_LENGTH)
        if self.format != "markdown":
            raise invalid("content.format", "must be markdown")

    def to_dict(self) -> dict[str, str]:
        return {"format": self.format, "text": self.text}


@dataclass(frozen=True, kw_only=True)
class SupervisorSource:
    endpoint_id: EndpointId
    native_session_id: NativeSessionId
    message_id: NativeMessageId
    user_message_id: NativeMessageId | None = None
    turn_group_id: str | None = None
    adapter: str = "chatgpt_web"

    def __post_init__(self) -> None:
        require_type(self.endpoint_id, EndpointId, "source.endpoint_id")
        require_type(self.native_session_id, NativeSessionId, "source.native_session_id")
        require_type(self.message_id, NativeMessageId, "source.message_id")
        if self.user_message_id is not None:
            require_type(self.user_message_id, NativeMessageId, "source.user_message_id")
        if self.turn_group_id is not None:
            require_text(self.turn_group_id, "source.turn_group_id", MAX_ID_LENGTH)
        if self.adapter != "chatgpt_web":
            raise invalid("source.adapter", "must be chatgpt_web")

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "endpoint_id": str(self.endpoint_id),
            "native_session_id": str(self.native_session_id),
            "message_id": str(self.message_id),
            "user_message_id": str(self.user_message_id) if self.user_message_id is not None else None,
            "turn_group_id": self.turn_group_id,
        }


@dataclass(frozen=True, kw_only=True)
class SupervisorEnvelope:
    submission_id: SubmissionId
    source: SupervisorSource
    content: Content
    schema_version: int = 1

    def __post_init__(self) -> None:
        require_type(self.submission_id, SubmissionId, "submission_id")
        require_type(self.source, SupervisorSource, "source")
        require_type(self.content, Content, "content")
        # The existing validator is the authority, including version and exact
        # field sets. No parallel Supervisor v1 schema is introduced here.
        validate_envelope(self.to_dict())

    @classmethod
    def from_dict(cls, raw: object) -> "SupervisorEnvelope":
        normalized = validate_envelope(raw)
        source = normalized["source"]
        return cls(
            submission_id=SubmissionId(normalized["submission_id"]),
            source=SupervisorSource(
                endpoint_id=EndpointId(source["endpoint_id"]),
                native_session_id=NativeSessionId(source["native_session_id"]),
                message_id=NativeMessageId(source["message_id"]),
                user_message_id=NativeMessageId(source["user_message_id"]) if source["user_message_id"] is not None else None,
                turn_group_id=source["turn_group_id"],
            ),
            content=Content(**normalized["content"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "submission_id": str(self.submission_id),
            "source": self.source.to_dict(),
            "content": self.content.to_dict(),
        }


@dataclass(frozen=True, kw_only=True)
class AgentReportEnvelope:
    report_id: ReportId
    task_id: TaskId
    kind: ReportKind
    content: Content
    outcome: ReportOutcome | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        require_type(self.report_id, ReportId, "report_id")
        require_type(self.task_id, TaskId, "task_id")
        require_type(self.kind, ReportKind, "kind")
        require_type(self.content, Content, "content")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise invalid("schema_version", "must be integer 1")
        if self.kind is ReportKind.FINAL:
            require_type(self.outcome, ReportOutcome, "outcome")
        elif self.outcome is not None:
            raise invalid("outcome", "must be null for non-final reports")

    @property
    def requires_return(self) -> bool:
        """Classification only; does not send or process the report."""
        return self.kind is not ReportKind.PROGRESS

    @classmethod
    def from_dict(cls, raw: object) -> "AgentReportEnvelope":
        document = exact_fields(
            raw,
            ("schema_version", "report_id", "task_id", "kind", "outcome", "content"),
            "$",
        )
        content = exact_fields(document["content"], ("format", "text"), "content")
        try:
            kind = ReportKind(document["kind"])
        except (ValueError, TypeError) as exc:
            raise invalid("kind", "unsupported report kind") from exc
        try:
            outcome = ReportOutcome(document["outcome"]) if document["outcome"] is not None else None
        except (ValueError, TypeError) as exc:
            raise invalid("outcome", "unsupported report outcome") from exc
        return cls(
            schema_version=document["schema_version"],
            report_id=ReportId(document["report_id"]),
            task_id=TaskId(document["task_id"]),
            kind=kind,
            outcome=outcome,
            content=Content(**content),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "report_id": str(self.report_id),
            "task_id": str(self.task_id),
            "kind": self.kind.value,
            "outcome": self.outcome.value if self.outcome is not None else None,
            "content": self.content.to_dict(),
        }
