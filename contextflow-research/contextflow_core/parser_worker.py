"""Reliable Parser Worker over the durable B1 ParseJob repository."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import math
import time
import uuid

from .contracts import (
    ErrorInfo,
    InstructionParser,
    MessageId,
    MessageKind,
    MessageReader,
    ParseResult,
    ParseStatus,
    ProcessingState,
)
from .errors import ContextFlowError
from .parse_jobs import (
    AttemptDecision,
    ParseClaim,
    SQLiteParseJobRepository,
)


@dataclass(frozen=True)
class ParserWorkerConfig:
    lease_duration_ms: int = 30_000
    execution_timeout_ms: int = 10_000
    submit_margin_ms: int = 5_000
    cancel_grace_ms: int = 1_000
    max_attempts: int = 3
    retry_base_ms: int = 1_000
    retry_cap_ms: int = 30_000
    discover_limit: int = 100
    poll_interval_ms: int = 500

    def __post_init__(self) -> None:
        for field, minimum in (
            ("lease_duration_ms", 1),
            ("execution_timeout_ms", 1),
            ("submit_margin_ms", 0),
            ("cancel_grace_ms", 0),
            ("max_attempts", 1),
            ("retry_base_ms", 0),
            ("retry_cap_ms", 0),
            ("discover_limit", 1),
            ("poll_interval_ms", 1),
        ):
            _integer(getattr(self, field), field, minimum)
        if self.retry_base_ms > self.retry_cap_ms:
            raise _argument("retry_base_ms", "must not exceed retry_cap_ms")
        if self.execution_timeout_ms + self.submit_margin_ms >= self.lease_duration_ms:
            raise _argument(
                "execution_timeout_ms",
                "execution timeout plus submission margin must be less than lease duration",
            )


class CycleStatus(str, Enum):
    IDLE = "idle"
    RETRY_SCHEDULED = "retry_scheduled"
    FINALIZED = "finalized"
    STALLED = "stalled"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class CycleOutcome:
    status: CycleStatus
    message_id: MessageId | None = None
    parse_status: ParseStatus | None = None
    error: ErrorInfo | None = None


class ParserWorker:
    def __init__(
        self,
        *,
        repository: SQLiteParseJobRepository,
        messages: MessageReader,
        parser: InstructionParser,
        config: ParserWorkerConfig | None = None,
        owner: str | None = None,
        known_agents: tuple[str, ...] | None = None,
    ) -> None:
        if not isinstance(repository, SQLiteParseJobRepository):
            raise _argument("repository", "must be a SQLiteParseJobRepository")
        if not isinstance(parser, InstructionParser):
            raise _argument("parser", "must implement InstructionParser")
        self.repository = repository
        self.messages = messages
        self.parser = parser
        self.config = config or ParserWorkerConfig()
        if not isinstance(self.config, ParserWorkerConfig):
            raise _argument("config", "must be a ParserWorkerConfig")
        self.owner = owner or f"parser-{uuid.uuid4().hex}"
        _text(self.owner, "owner")
        if known_agents is not None and (
            not isinstance(known_agents, tuple)
            or any(not isinstance(item, str) or not item.strip() for item in known_agents)
        ):
            raise _argument("known_agents", "must be a tuple of non-empty strings or None")
        self.known_agents = known_agents
        self._stalled_task: asyncio.Task[object] | None = None
        self._stalled = False
        self._stop = asyncio.Event()

    async def run_once(self) -> CycleOutcome:
        self._reap_stalled_task()
        if self._stalled:
            raise ContextFlowError(
                "PARSER_WORKER_STALLED",
                "Parser Worker has an uncooperative in-flight Parser call",
            )
        await self.repository.discover_pending(
            limit=self.config.discover_limit,
            max_attempts=self.config.max_attempts,
        )
        request_id = f"claim-{uuid.uuid4().hex}"
        claim = await self.repository.claim_next(
            self.owner,
            request_id,
            lease_duration_ms=self.config.lease_duration_ms,
        )
        if claim is None:
            return CycleOutcome(status=CycleStatus.IDLE)
        return await self._process_claim(claim)

    async def _process_claim(self, claim: ParseClaim) -> CycleOutcome:
        remaining_lease_ms = claim.lease_expires_at_ms - int(time.time() * 1000)
        required_ms = (
            self.config.execution_timeout_ms + self.config.submit_margin_ms
        )
        if remaining_lease_ms <= required_ms:
            return CycleOutcome(
                status=CycleStatus.SKIPPED,
                message_id=claim.message_id,
                error=ErrorInfo(
                    code="PARSE_LEASE_TOO_SHORT",
                    message="remaining parse lease is too short to start Parser execution",
                    retryable=True,
                ),
            )
        message = await self.messages.get_message(claim.message_id)
        if (
            message is None
            or message.kind is not MessageKind.SUPERVISOR_INSTRUCTION
            or message.processing_state is not ProcessingState.PENDING
        ):
            return CycleOutcome(
                status=CycleStatus.SKIPPED,
                message_id=claim.message_id,
                error=ErrorInfo(
                    code="PARSE_MESSAGE_INELIGIBLE",
                    message="source Message is missing or no longer pending Supervisor work",
                ),
            )

        authorization = await self.repository.begin_attempt(claim)
        if authorization is AttemptDecision.ALREADY_RECORDED:
            # A repeated/uncertain authorization is deliberately not another call.
            return await self._schedule_or_exhaust(
                claim,
                ErrorInfo(
                    code="PARSER_ATTEMPT_UNCONFIRMED",
                    message="this claim generation already registered a Parser call",
                    retryable=True,
                ),
            )
        if authorization is AttemptDecision.EXHAUSTED:
            return await self._finalize_exhausted(claim)

        parser_task: asyncio.Task[object] = asyncio.create_task(
            self.parser.parse(message.envelope.content.text, self.known_agents)
        )
        try:
            result = await asyncio.wait_for(
                asyncio.shield(parser_task),
                timeout=self.config.execution_timeout_ms / 1000,
            )
        except asyncio.TimeoutError:
            stopped = await self._cancel_parser_task(parser_task)
            if not stopped:
                self._stalled = True
                self._stalled_task = parser_task
                return CycleOutcome(
                    status=CycleStatus.STALLED,
                    message_id=claim.message_id,
                    error=ErrorInfo(
                        code="PARSER_TIMEOUT",
                        message="Parser exceeded its execution deadline",
                        retryable=True,
                    ),
                )
            return await self._schedule_or_exhaust(
                claim,
                ErrorInfo(
                    code="PARSER_TIMEOUT",
                    message="Parser exceeded its execution deadline",
                    retryable=True,
                ),
            )
        except asyncio.CancelledError:
            stopped = await self._cancel_parser_task(parser_task)
            if not stopped:
                self._stalled = True
                self._stalled_task = parser_task
                # Keep the claim until lease expiry.  Releasing it while the
                # uncooperative call is still live could authorize overlapping
                # work for the same source Message.
                raise
            try:
                await self._schedule_or_exhaust(
                    claim,
                    ErrorInfo(
                        code="PARSER_CANCELLED",
                        message="Parser Worker was cancelled during execution",
                        retryable=True,
                    ),
                )
            except ContextFlowError:
                pass
            raise
        except ContextFlowError as exc:
            if exc.retryable:
                return await self._schedule_or_exhaust(claim, ErrorInfo.from_error(exc))
            return await self._finalize_error(claim, ErrorInfo.from_error(exc))
        except Exception as exc:
            return await self._finalize_error(
                claim,
                ErrorInfo(
                    code="PARSER_EXECUTION_ERROR",
                    message="Parser execution failed",
                    details={"exception_type": type(exc).__name__},
                ),
            )

        if not isinstance(result, ParseResult):
            return await self._finalize_error(
                claim,
                ErrorInfo(
                    code="PARSER_CONTRACT_VIOLATION",
                    message="Parser did not return a ParseResult",
                    details={"return_type": type(result).__name__},
                ),
            )
        stored = await self.repository.finalize(claim, result)
        return CycleOutcome(
            status=CycleStatus.FINALIZED,
            message_id=stored.message_id,
            parse_status=stored.result.status,
            error=stored.result.error,
        )

    async def _schedule_or_exhaust(
        self, claim: ParseClaim, error: ErrorInfo
    ) -> CycleOutcome:
        job = await self.repository.get_job(claim.message_id)
        if job is None:
            raise ContextFlowError("PARSE_JOB_NOT_FOUND", "source parse job disappeared")
        if job.attempt_count >= job.max_attempts:
            return await self._finalize_exhausted(claim)
        delay = min(
            self.config.retry_cap_ms,
            self.config.retry_base_ms * (2 ** max(0, job.attempt_count - 1)),
        )
        hint = error.details.get("retry_after_ms")
        if (
            type(hint) is int
            and 0 <= hint <= 3_600_000
        ):
            delay = max(delay, hint)
        await self.repository.schedule_retry(claim, error, delay_ms=delay)
        return CycleOutcome(
            status=CycleStatus.RETRY_SCHEDULED,
            message_id=claim.message_id,
            error=error,
        )

    async def _finalize_exhausted(self, claim: ParseClaim) -> CycleOutcome:
        return await self._finalize_error(
            claim,
            ErrorInfo(
                code="PARSE_ATTEMPTS_EXHAUSTED",
                message="Parser retry budget is exhausted",
            ),
        )

    async def _finalize_error(
        self, claim: ParseClaim, error: ErrorInfo
    ) -> CycleOutcome:
        result = ParseResult(status=ParseStatus.FAILED, error=error)
        stored = await self.repository.finalize(claim, result)
        return CycleOutcome(
            status=CycleStatus.FINALIZED,
            message_id=stored.message_id,
            parse_status=stored.result.status,
            error=error,
        )

    async def _cancel_parser_task(self, task: asyncio.Task[object]) -> bool:
        if task.done():
            _consume_task(task)
            return True
        task.cancel()
        done, _ = await asyncio.wait(
            {task}, timeout=self.config.cancel_grace_ms / 1000
        )
        if task in done:
            _consume_task(task)
            return True
        task.add_done_callback(_consume_task)
        return False

    def _reap_stalled_task(self) -> None:
        task = self._stalled_task
        if task is not None and task.done():
            _consume_task(task)
            self._stalled_task = None
            # An instance that allowed an uncooperative call to outlive its
            # deadline remains disabled; callers must construct a fresh parser.

    async def run(self) -> None:
        while not self._stop.is_set():
            outcome = await self.run_once()
            if outcome.status is CycleStatus.IDLE:
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), self.config.poll_interval_ms / 1000
                    )
                except asyncio.TimeoutError:
                    pass

    def stop(self) -> None:
        self._stop.set()


def _consume_task(task: asyncio.Task[object]) -> None:
    try:
        task.result()
    except (BaseException, asyncio.CancelledError):
        pass


def _argument(field: str, reason: str) -> ContextFlowError:
    return ContextFlowError(
        "INVALID_ARGUMENT",
        f"invalid {field}: {reason}",
        details={"field": field},
    )


def _integer(value: object, field: str, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise _argument(field, f"must be an integer >= {minimum}")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise _argument(field, "must be a non-empty string of at most 256 characters")
    return value
