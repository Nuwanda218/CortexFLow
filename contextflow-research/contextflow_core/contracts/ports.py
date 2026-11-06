"""Structural Ports only. Ellipsis bodies do not implement any service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from .envelopes import ReportOutcome
from .ids import AgentId, EndpointId, MessageId, TaskId
from .models import (
    AcceptResult,
    AgentDescriptor,
    CreateTasksResult,
    Delivery,
    DeliveryOutcome,
    EndpointIdentity,
    ErrorInfo,
    Message,
    MessageKind,
    MessageSubmission,
    ParseResult,
    ProcessResult,
    SupervisorSubmission,
    Task,
)


@runtime_checkable
class MessageReader(Protocol):
    async def get_message(self, message_id: MessageId) -> Message | None: ...


@runtime_checkable
class MessageStore(MessageReader, Protocol):
    """A: immutable ingress plus passive processing markers.

    Accept only after durable commit. Compare full envelopes under submission_id
    (Supervisor) or (source_agent_id, report_id) (Agent). An identical duplicate
    returns the original Message ID; conflicting input never overwrites it.
    Validate Supervisor Endpoint identity, but never look up Tasks or call LLMs.
    Agent ingress must already have passed D's trusted-source/Task checks.
    """

    async def accept_message(self, submission: MessageSubmission) -> AcceptResult: ...

    async def list_pending(self, kind: MessageKind, limit: int) -> tuple[Message, ...]: ...

    async def mark_processed(self, message_id: MessageId) -> None:
        """Idempotent, after all downstream durable steps; never means sent/completed."""
        ...

    async def mark_blocked(self, message_id: MessageId, error: ErrorInfo) -> None: ...


@runtime_checkable
class LlmExtractionPort(Protocol):
    """Provider boundary. B, not this Port, validates raw structured output."""

    async def extract(self, text: str, known_agents: tuple[str, ...] | None = None) -> object: ...


@runtime_checkable
class InstructionParser(Protocol):
    """B: no default target, partial unresolved instructions, Task IDs, or I/O routing."""

    async def parse(self, text: str, known_agents: tuple[str, ...] | None = None) -> ParseResult: ...


@runtime_checkable
class EndpointIdentityReader(Protocol):
    """Read existing Registry identity only; do not create bindings or probe tabs."""

    async def get_identity(self, endpoint_id: EndpointId) -> EndpointIdentity | None: ...


@runtime_checkable
class AgentDirectory(Protocol):
    """Stable identity authority, not a browser Endpoint Registry.

    Unknown target returns None; ambiguous references raise AGENT_AMBIGUOUS.
    Known but offline targets remain in the catalog with available=false.
    Never create an Agent to satisfy a Parser output or substitute another ID.
    """

    async def known_agents(self) -> tuple[str, ...]: ...

    async def resolve(self, target: str) -> AgentDescriptor | None: ...

    async def get_agent(self, agent_id: AgentId) -> AgentDescriptor | None: ...


@runtime_checkable
class AgentGateway(Protocol):
    """task_id is the receiver's durable idempotency key.

    Same ID/body must not execute twice; same ID/different body must conflict.
    confirmed means durable acceptance, NOT Task completion. unknown must not
    claim a safe resend. pending means known unavailability without acceptance.
    """

    async def send_task(self, agent_id: AgentId, task_id: TaskId, content: str) -> DeliveryOutcome: ...


@runtime_checkable
class TaskQuery(Protocol):
    async def get_task(self, task_id: TaskId) -> Task | None: ...

    async def get_tasks_for_message(self, message_id: MessageId) -> tuple[Task, ...]:
        """Return the entire committed batch in instruction order, or ().

        Any nonempty result is authoritative even if Message is still pending.
        A processed Supervisor Message with () is inconsistent, not permission
        to reparse or recreate Tasks. Partial committed batches are forbidden.
        """
        ...


@runtime_checkable
class TaskStatus(Protocol):
    async def finish_task(
        self,
        task_id: TaskId,
        final_report_message_id: MessageId,
        outcome: ReportOutcome,
    ) -> Task:
        """C's atomic final-state update, called only after D validates the report.

        Same final Message/outcome is idempotent. A different final Message or
        changed outcome raises FINAL_REPORT_CONFLICT, preserving the first one.
        Final reports may precede dispatch receipts; a late receipt cannot undo
        a terminal state. Do not infer completion from Delivery.confirmed.
        """
        ...


@runtime_checkable
class TaskDispatcher(Protocol):
    """C: all targets must be legal before a whole batch is committed.

    Commit every Task, initial Delivery and candidate intent_fingerprint
    atomically under source_message_id. Matching re-entry returns the original
    batch; a changed candidate raises/returns TASK_BATCH_CONFLICT, never an
    append/replacement. Agent availability is distinct from target legality.
    Actual Agent receipts after admission need not be atomic across endpoints.
    """

    async def create_tasks(self, source_message_id: MessageId, parsed: ParseResult) -> CreateTasksResult: ...

    async def dispatch_pending(self, source_message_id: MessageId) -> tuple[Delivery, ...]:
        """Only eligible pending deliveries; persist sending before external I/O.

        Preserve mixed confirmed/failed/unknown results, never rollback accepted
        Tasks. No blind retry, automatic compensation or target substitution.
        """
        ...

    async def list_pending_batches(self, limit: int) -> tuple[MessageId, ...]: ...

    async def recover_inflight(self) -> tuple[Delivery, ...]:
        """Convert this module's stale sending to unknown, without sending."""
        ...


@runtime_checkable
class SupervisorGateway(Protocol):
    """E: one multiplexed connection, existing Supervisor Bridge wire protocol.

    Submissions and request replies must be demultiplexed without blocking
    ingress acknowledgements on long sends. No hello/receiver-registration
    protocol change. No forced draft replacement or automatic unknown resend.
    """

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    def submissions(self) -> AsyncIterator[SupervisorSubmission]: ...

    async def acknowledge(self, submission_id: str | None, result: AcceptResult) -> None:
        """Map to the existing submission_ack fields; Core message_id stays internal."""
        ...

    async def send_message_to_endpoint(self, endpoint_id: EndpointId, text: str) -> DeliveryOutcome:
        """confirmed requires Adapter send verification; preserve SEND_UNVERIFIED.

        Error code/retryable alone cannot prove absence of send side effects.
        Timeout, connection loss or stale sending with possible effects is
        unknown. Default force=false; an existing draft is not overwritten.
        """
        ...


@runtime_checkable
class ReportReturn(Protocol):
    """D: persist progress; automatically return question/blocked/final.

    Validate Task existence and trusted source_agent_id before Store acceptance.
    Only one effective final is allowed. If an already stored report conflicts
    during processing/recovery, retain it and mark blocked; never overwrite the
    Task's first final. No LLM summary, new Task or Agent coordination.
    """

    async def accept_report(self, source_agent_id: AgentId, envelope: object) -> AcceptResult: ...

    async def process_report(self, message_id: MessageId) -> ProcessResult:
        """Replayable state/return-intent steps before marking Message processed.

        Follow Task.source_message_id to its Supervisor Envelope Endpoint ID.
        One report Message has one initial return Delivery. Freeze its outbound
        text when creating that Delivery; do not duplicate it on replay.
        """
        ...

    async def deliver_pending(self, limit: int) -> tuple[Delivery, ...]: ...

    async def recover_inflight(self) -> tuple[Delivery, ...]: ...


@runtime_checkable
class MessageProcessor(Protocol):
    """E's interface only, not a Runtime or recovery implementation.

    On a pending Supervisor Message, query committed Tasks BEFORE parsing.
    Existing batch: no LLM call, no append/replacement. No batch: parsing may
    yield a different uncommitted candidate; choose one whole valid result and
    let C's atomic commit/signature establish the durable intent. blocked work
    is not automatically reparsed. Do not infer missing Tasks from flags alone.
    """

    async def accept_supervisor(self, envelope: object) -> AcceptResult:
        """Persist first, acknowledge promptly; do not gate acceptance on parsing."""
        ...

    async def process_message(self, message_id: MessageId) -> ProcessResult: ...

    async def recover_once(self, limit: int) -> None:
        """First recover C/D in-flight sends as unknown; then resume pending work.

        No automatic retransmission of unknown/confirmed/failed deliveries.
        Recovery does not plan tasks or orchestrate Agent collaboration.
        """
        ...
