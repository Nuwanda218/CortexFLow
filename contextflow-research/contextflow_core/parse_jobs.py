"""Durable Parser job repository with lease/generation fencing.

This module orchestrates only parsing.  It never calls an LLM, creates Tasks,
or sends to an Agent.  Every owner-specific write rechecks the source Message,
claim generation and live lease in the same SQLite write transaction.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import json
import math
from pathlib import Path
import sqlite3
import threading
from typing import Any, Callable, Protocol, TypeVar, runtime_checkable

from .contracts import ErrorInfo, MessageId, ParseResult, ParseStatus
from .database_schema import ensure_schema_v2
from .errors import ContextFlowError
from .parse_result_codec import (
    EncodedParseResult,
    decode_parse_result,
    encode_parse_result,
)

_NOW_MS_SQL = "CAST((julianday('now') - 2440587.5) * 86400000 AS INTEGER)"
_T = TypeVar("_T")


class JobState(str, Enum):
    READY = "ready"
    PROCESSING = "processing"
    RETRY_WAIT = "retry_wait"
    COMPLETED = "completed"


class AttemptDecision(str, Enum):
    AUTHORIZED = "authorized"
    ALREADY_RECORDED = "already_recorded"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True)
class ParseClaim:
    message_id: MessageId
    owner: str
    claim_request_id: str
    claim_generation: int
    lease_expires_at_ms: int
    attempt_count: int
    max_attempts: int


@dataclass(frozen=True)
class StoredParseResult:
    message_id: MessageId
    result: ParseResult
    completed_at_ms: int


@runtime_checkable
class ParseResultReader(Protocol):
    async def get_final_result(
        self, message_id: MessageId
    ) -> StoredParseResult | None: ...


@dataclass(frozen=True)
class ParseJob:
    message_id: MessageId
    state: JobState
    claim_generation: int
    lease_owner: str | None
    claim_request_id: str | None
    lease_expires_at_ms: int | None
    attempt_count: int
    last_attempt_generation: int | None
    max_attempts: int
    next_attempt_at_ms: int | None
    last_error: ErrorInfo | None
    formal_result: ParseResult | None
    created_at_ms: int
    updated_at_ms: int
    completed_at_ms: int | None


class SQLiteParseJobRepository:
    """One serialized SQLite connection; separate instances coordinate in SQLite."""

    def __init__(
        self,
        path: str | Path,
        *,
        busy_timeout: float = 5.0,
    ) -> None:
        if str(path) == ":memory:" or str(path).startswith("file:"):
            raise _argument("path", "must name a persistent database file")
        timeout = _duration(busy_timeout, "busy_timeout", allow_zero=True)
        self.path = Path(path).expanduser()
        self._lock = threading.Lock()
        self._closed = False
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self.path,
                timeout=timeout,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            self._connection = connection
            ensure_schema_v2(connection)
        except (OSError, sqlite3.Error) as exc:
            if connection is not None:
                connection.close()
            if isinstance(exc, sqlite3.Error):
                raise _database_error(exc, "initialize") from exc
            raise ContextFlowError(
                "MESSAGE_STORE_OPEN_FAILED",
                "cannot open parse job database",
                details={"operation": "initialize"},
            ) from exc
        except BaseException:
            if connection is not None:
                connection.close()
            raise

    async def _run(self, operation: Callable[[], _T]) -> _T:
        """Do not let cancellation leave an unobserved SQLite worker behind."""

        task = asyncio.create_task(asyncio.to_thread(operation))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            except BaseException:
                # The caller still observes cancellation.  Waiting here only
                # makes the background database effect/rollback determinate.
                pass
            raise

    async def close(self) -> None:
        await self._run(self._close)

    def _close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._connection.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise ContextFlowError("PARSE_JOB_REPOSITORY_CLOSED", "parse job database is closed")

    def _rollback(self) -> sqlite3.Error | None:
        if self._closed or not self._connection.in_transaction:
            return None
        try:
            self._connection.execute("ROLLBACK")
        except sqlite3.Error as exc:
            self._closed = True
            try:
                self._connection.close()
            except sqlite3.Error:
                pass
            return exc
        return None

    async def discover_pending(self, *, limit: int, max_attempts: int) -> int:
        _integer(limit, "limit", minimum=0)
        _integer(max_attempts, "max_attempts", minimum=1)
        if limit == 0:
            return 0
        return await self._run(lambda: self._discover_pending(limit, max_attempts))

    def _discover_pending(self, limit: int, max_attempts: int) -> int:
        with self._lock:
            self._ensure_open()
            connection = self._connection
            try:
                connection.execute("BEGIN IMMEDIATE")
                now = _database_now(connection)
                candidates = connection.execute(
                    """
                    SELECT m.message_id
                    FROM messages AS m
                    WHERE m.kind = 'supervisor_instruction'
                      AND m.processing_state = 'pending'
                      AND NOT EXISTS (
                          SELECT 1 FROM message_parse_jobs AS j
                          WHERE j.message_id = m.message_id
                      )
                    ORDER BY m.received_at, m.message_id
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                created = 0
                for candidate in candidates:
                    message_id = candidate["message_id"]
                    cursor = connection.execute(
                        """
                        INSERT INTO message_parse_jobs (
                            message_id, job_state, claim_generation, lease_owner,
                            claim_request_id, lease_expires_at_ms, attempt_count,
                            last_attempt_generation, max_attempts, next_attempt_at_ms,
                            last_error_json, result_status, instructions_json,
                            result_error_json, intent_fingerprint, created_at_ms,
                            updated_at_ms, completed_at_ms
                        ) VALUES (?, 'ready', 0, NULL, NULL, NULL, 0, NULL,
                                  ?, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?, NULL)
                        """,
                        (message_id, max_attempts, now, now),
                    )
                    stored = connection.execute(
                        "SELECT * FROM message_parse_jobs WHERE message_id=?",
                        (message_id,),
                    ).fetchone()
                    expected = ParseJob(
                        message_id=MessageId(message_id),
                        state=JobState.READY,
                        claim_generation=0,
                        lease_owner=None,
                        claim_request_id=None,
                        lease_expires_at_ms=None,
                        attempt_count=0,
                        last_attempt_generation=None,
                        max_attempts=max_attempts,
                        next_attempt_at_ms=None,
                        last_error=None,
                        formal_result=None,
                        created_at_ms=now,
                        updated_at_ms=now,
                        completed_at_ms=None,
                    )
                    if (
                        cursor.rowcount != 1
                        or stored is None
                        or _decode_job(stored) != expected
                    ):
                        raise ContextFlowError(
                            "PARSE_JOB_WRITE_FAILED",
                            "discovery did not create the expected parse job",
                            details={"operation": "verify_discover"},
                        )
                    created += 1
                connection.execute("COMMIT")
                return created
            except sqlite3.Error as exc:
                rollback_error = self._rollback()
                raise _database_error(exc, "discover", rollback_error) from exc
            except BaseException:
                self._rollback()
                raise

    async def get_job(self, message_id: MessageId) -> ParseJob | None:
        _message_id(message_id)
        return await self._run(lambda: self._get_job(message_id))

    def _get_job(self, message_id: MessageId) -> ParseJob | None:
        with self._lock:
            self._ensure_open()
            try:
                row = self._connection.execute(
                    "SELECT * FROM message_parse_jobs WHERE message_id=?",
                    (str(message_id),),
                ).fetchone()
                return _decode_job(row) if row is not None else None
            except sqlite3.Error as exc:
                raise _database_error(exc, "get_job") from exc

    async def get_final_result(self, message_id: MessageId) -> StoredParseResult | None:
        job = await self.get_job(message_id)
        if job is None or job.state is not JobState.COMPLETED:
            return None
        if job.formal_result is None or job.completed_at_ms is None:
            raise _corrupt("completed job has no formal result")
        return StoredParseResult(
            message_id=job.message_id,
            result=job.formal_result,
            completed_at_ms=job.completed_at_ms,
        )

    async def claim_next(
        self,
        owner: str,
        claim_request_id: str,
        *,
        lease_duration_ms: int,
    ) -> ParseClaim | None:
        _text(owner, "owner")
        _text(claim_request_id, "claim_request_id")
        _integer(lease_duration_ms, "lease_duration_ms", minimum=1)
        return await self._run(
            lambda: self._claim_next(owner, claim_request_id, lease_duration_ms)
        )

    def _claim_next(
        self,
        owner: str,
        claim_request_id: str,
        lease_duration_ms: int,
    ) -> ParseClaim | None:
        with self._lock:
            self._ensure_open()
            connection = self._connection
            try:
                connection.execute("BEGIN IMMEDIATE")
                now = _database_now(connection)
                existing = connection.execute(
                    """
                    SELECT * FROM message_parse_jobs
                    WHERE job_state='processing' AND lease_owner=?
                      AND claim_request_id=? AND lease_expires_at_ms>?
                    """,
                    (owner, claim_request_id, now),
                ).fetchone()
                if existing is not None:
                    claim = _claim_from_row(existing)
                    connection.execute("COMMIT")
                    return claim

                row = connection.execute(
                    """
                    SELECT j.*
                    FROM message_parse_jobs AS j
                    JOIN messages AS m ON m.message_id = j.message_id
                    WHERE m.kind='supervisor_instruction'
                      AND m.processing_state='pending'
                      AND (
                          j.job_state='ready'
                          OR (j.job_state='retry_wait' AND j.next_attempt_at_ms <= ?)
                          OR (j.job_state='processing' AND j.lease_expires_at_ms <= ?)
                      )
                    ORDER BY m.received_at, j.message_id
                    LIMIT 1
                    """,
                    (now, now),
                ).fetchone()
                if row is None:
                    connection.execute("COMMIT")
                    return None

                generation = row["claim_generation"] + 1
                expiry = now + lease_duration_ms
                cursor = connection.execute(
                    """
                    UPDATE message_parse_jobs
                    SET job_state='processing', claim_generation=?, lease_owner=?,
                        claim_request_id=?, lease_expires_at_ms=?,
                        next_attempt_at_ms=NULL, updated_at_ms=?
                    WHERE message_id=? AND claim_generation=?
                      AND job_state=?
                    """,
                    (
                        generation,
                        owner,
                        claim_request_id,
                        expiry,
                        now,
                        row["message_id"],
                        row["claim_generation"],
                        row["job_state"],
                    ),
                )
                stored = connection.execute(
                    "SELECT * FROM message_parse_jobs WHERE message_id=?",
                    (row["message_id"],),
                ).fetchone()
                if cursor.rowcount != 1 or stored is None:
                    raise ContextFlowError(
                        "PARSE_JOB_WRITE_FAILED",
                        "claim did not update the expected parse job",
                        details={"operation": "claim"},
                    )
                claim = _claim_from_row(stored)
                if claim != ParseClaim(
                    message_id=MessageId(row["message_id"]),
                    owner=owner,
                    claim_request_id=claim_request_id,
                    claim_generation=generation,
                    lease_expires_at_ms=expiry,
                    attempt_count=row["attempt_count"],
                    max_attempts=row["max_attempts"],
                ):
                    raise ContextFlowError(
                        "PARSE_JOB_WRITE_FAILED",
                        "claim did not retain the expected ownership token",
                        details={"operation": "verify_claim"},
                    )
                connection.execute("COMMIT")
                return claim
            except sqlite3.Error as exc:
                rollback_error = self._rollback()
                raise _database_error(exc, "claim", rollback_error) from exc
            except BaseException:
                self._rollback()
                raise

    async def find_claim(
        self, owner: str, claim_request_id: str
    ) -> ParseClaim | None:
        _text(owner, "owner")
        _text(claim_request_id, "claim_request_id")
        return await self._run(lambda: self._find_claim(owner, claim_request_id))

    def _find_claim(self, owner: str, claim_request_id: str) -> ParseClaim | None:
        with self._lock:
            self._ensure_open()
            try:
                row = self._connection.execute(
                    """
                    SELECT * FROM message_parse_jobs
                    WHERE job_state='processing' AND lease_owner=?
                      AND claim_request_id=?
                    """,
                    (owner, claim_request_id),
                ).fetchone()
                return _claim_from_row(row) if row is not None else None
            except sqlite3.Error as exc:
                raise _database_error(exc, "find_claim") from exc

    async def begin_attempt(self, claim: ParseClaim) -> AttemptDecision:
        _claim(claim)
        return await self._run(lambda: self._begin_attempt(claim))

    def _begin_attempt(self, claim: ParseClaim) -> AttemptDecision:
        with self._lock:
            self._ensure_open()
            connection = self._connection
            try:
                connection.execute("BEGIN IMMEDIATE")
                now = _database_now(connection)
                row = _select_owned(connection, claim, now)
                if row is None:
                    raise _claim_lost()
                if row["last_attempt_generation"] == claim.claim_generation:
                    connection.execute("COMMIT")
                    return AttemptDecision.ALREADY_RECORDED
                if row["attempt_count"] >= row["max_attempts"]:
                    connection.execute("COMMIT")
                    return AttemptDecision.EXHAUSTED
                cursor = connection.execute(
                    """
                    UPDATE message_parse_jobs
                    SET attempt_count=attempt_count+1,
                        last_attempt_generation=?, updated_at_ms=?
                    WHERE message_id=? AND job_state='processing'
                      AND lease_owner=? AND claim_request_id=?
                      AND claim_generation=? AND lease_expires_at_ms>?
                      AND EXISTS (
                          SELECT 1 FROM messages AS m
                          WHERE m.message_id=message_parse_jobs.message_id
                            AND m.kind='supervisor_instruction'
                            AND m.processing_state='pending'
                      )
                    """,
                    (
                        claim.claim_generation,
                        now,
                        str(claim.message_id),
                        claim.owner,
                        claim.claim_request_id,
                        claim.claim_generation,
                        now,
                    ),
                )
                if cursor.rowcount != 1:
                    raise _claim_lost()
                stored = connection.execute(
                    "SELECT * FROM message_parse_jobs WHERE message_id=?",
                    (str(claim.message_id),),
                ).fetchone()
                if stored is None:
                    raise ContextFlowError(
                        "PARSE_JOB_WRITE_FAILED",
                        "attempt authorization did not retain the parse job",
                        details={"operation": "verify_begin_attempt"},
                    )
                job = _decode_job(stored)
                if (
                    job.state is not JobState.PROCESSING
                    or job.claim_generation != claim.claim_generation
                    or job.lease_owner != claim.owner
                    or job.claim_request_id != claim.claim_request_id
                    or job.lease_expires_at_ms != row["lease_expires_at_ms"]
                    or job.attempt_count != row["attempt_count"] + 1
                    or job.last_attempt_generation != claim.claim_generation
                    or job.max_attempts != row["max_attempts"]
                    or job.next_attempt_at_ms is not None
                    or job.formal_result is not None
                    or job.completed_at_ms is not None
                ):
                    raise ContextFlowError(
                        "PARSE_JOB_WRITE_FAILED",
                        "attempt authorization did not store the expected state",
                        details={"operation": "verify_begin_attempt"},
                    )
                connection.execute("COMMIT")
                return AttemptDecision.AUTHORIZED
            except sqlite3.Error as exc:
                rollback_error = self._rollback()
                raise _database_error(exc, "begin_attempt", rollback_error) from exc
            except BaseException:
                self._rollback()
                raise

    async def schedule_retry(
        self,
        claim: ParseClaim,
        error: ErrorInfo,
        *,
        delay_ms: int,
    ) -> ParseJob:
        _claim(claim)
        _error(error)
        _integer(delay_ms, "delay_ms", minimum=0)
        error_json = _canonical_error(error)
        return await self._run(
            lambda: self._schedule_retry(claim, error_json, delay_ms)
        )

    def _schedule_retry(
        self, claim: ParseClaim, error_json: str, delay_ms: int
    ) -> ParseJob:
        with self._lock:
            self._ensure_open()
            connection = self._connection
            try:
                connection.execute("BEGIN IMMEDIATE")
                now = _database_now(connection)
                row = _select_owned(connection, claim, now)
                if row is None or row["last_attempt_generation"] != claim.claim_generation:
                    raise _claim_lost()
                if row["attempt_count"] >= row["max_attempts"]:
                    raise ContextFlowError(
                        "PARSE_ATTEMPTS_EXHAUSTED",
                        "parse retry budget is exhausted",
                    )
                cursor = connection.execute(
                    """
                    UPDATE message_parse_jobs
                    SET job_state='retry_wait', lease_owner=NULL,
                        claim_request_id=NULL, lease_expires_at_ms=NULL,
                        next_attempt_at_ms=?, last_error_json=?, updated_at_ms=?
                    WHERE message_id=? AND job_state='processing'
                      AND lease_owner=? AND claim_request_id=?
                      AND claim_generation=? AND lease_expires_at_ms>?
                      AND EXISTS (
                          SELECT 1 FROM messages AS m
                          WHERE m.message_id=message_parse_jobs.message_id
                            AND m.kind='supervisor_instruction'
                            AND m.processing_state='pending'
                      )
                    """,
                    (
                        now + delay_ms,
                        error_json,
                        now,
                        str(claim.message_id),
                        claim.owner,
                        claim.claim_request_id,
                        claim.claim_generation,
                        now,
                    ),
                )
                stored = connection.execute(
                    "SELECT * FROM message_parse_jobs WHERE message_id=?",
                    (str(claim.message_id),),
                ).fetchone()
                if cursor.rowcount != 1 or stored is None:
                    raise _claim_lost()
                job = _decode_job(stored)
                if (
                    job.state is not JobState.RETRY_WAIT
                    or job.claim_generation != claim.claim_generation
                    or job.attempt_count != row["attempt_count"]
                    or job.last_attempt_generation != row["last_attempt_generation"]
                    or job.max_attempts != row["max_attempts"]
                    or job.next_attempt_at_ms != now + delay_ms
                    or job.last_error is None
                    or _canonical_error(job.last_error) != error_json
                    or job.lease_owner is not None
                    or job.claim_request_id is not None
                    or job.lease_expires_at_ms is not None
                    or job.formal_result is not None
                    or job.completed_at_ms is not None
                ):
                    raise ContextFlowError(
                        "PARSE_JOB_WRITE_FAILED",
                        "retry did not store the expected parse job state",
                        details={"operation": "verify_retry"},
                    )
                connection.execute("COMMIT")
                return job
            except sqlite3.Error as exc:
                rollback_error = self._rollback()
                raise _database_error(exc, "schedule_retry", rollback_error) from exc
            except BaseException:
                self._rollback()
                raise

    async def finalize(
        self, claim: ParseClaim, result: ParseResult
    ) -> StoredParseResult:
        _claim(claim)
        encoded = encode_parse_result(result)
        return await self._run(lambda: self._finalize(claim, result, encoded))

    def _finalize(
        self,
        claim: ParseClaim,
        result: ParseResult,
        encoded: EncodedParseResult,
    ) -> StoredParseResult:
        with self._lock:
            self._ensure_open()
            connection = self._connection
            try:
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute(
                    "SELECT * FROM message_parse_jobs WHERE message_id=?",
                    (str(claim.message_id),),
                ).fetchone()
                if current is None:
                    raise _claim_lost()
                if current["job_state"] == JobState.COMPLETED.value:
                    stored = _stored_from_row(current)
                    if stored.result == result:
                        connection.execute("COMMIT")
                        return stored
                    raise ContextFlowError(
                        "PARSE_RESULT_CONFLICT",
                        "source Message already has a different formal ParseResult",
                    )

                now = _database_now(connection)
                owned = _select_owned(connection, claim, now)
                if owned is None:
                    raise _claim_lost()
                original_message = connection.execute(
                    """
                    SELECT kind, processing_state, processing_error_json
                    FROM messages WHERE message_id=?
                    """,
                    (str(claim.message_id),),
                ).fetchone()
                if original_message is None:
                    raise _claim_lost()
                current_attempt = (
                    owned["last_attempt_generation"] == claim.claim_generation
                )
                exhausted_recovery = (
                    result.status is ParseStatus.FAILED
                    and result.error is not None
                    and result.error.code == "PARSE_ATTEMPTS_EXHAUSTED"
                    and owned["attempt_count"] >= owned["max_attempts"]
                )
                if not current_attempt and not exhausted_recovery:
                    raise _claim_lost()

                cursor = connection.execute(
                    """
                    UPDATE message_parse_jobs
                    SET job_state='completed', lease_owner=NULL,
                        claim_request_id=NULL, lease_expires_at_ms=NULL,
                        next_attempt_at_ms=NULL, result_status=?,
                        instructions_json=?, result_error_json=?,
                        intent_fingerprint=?, updated_at_ms=?, completed_at_ms=?
                    WHERE message_id=? AND job_state='processing'
                      AND lease_owner=? AND claim_request_id=?
                      AND claim_generation=? AND lease_expires_at_ms>?
                      AND result_status IS NULL
                      AND EXISTS (
                          SELECT 1 FROM messages AS m
                          WHERE m.message_id=message_parse_jobs.message_id
                            AND m.kind='supervisor_instruction'
                            AND m.processing_state='pending'
                      )
                    """,
                    (
                        encoded.status,
                        encoded.instructions_json,
                        encoded.error_json,
                        encoded.fingerprint,
                        now,
                        now,
                        str(claim.message_id),
                        claim.owner,
                        claim.claim_request_id,
                        claim.claim_generation,
                        now,
                    ),
                )
                if cursor.rowcount != 1:
                    raise _claim_lost()

                message_state = connection.execute(
                    """
                    SELECT kind, processing_state, processing_error_json
                    FROM messages WHERE message_id=?
                    """,
                    (str(claim.message_id),),
                ).fetchone()
                expected_message = tuple(original_message)
                if result.status is not ParseStatus.PARSED:
                    error_json = encoded.error_json
                    message_cursor = connection.execute(
                        """
                        UPDATE messages
                        SET processing_state='blocked', processing_error_json=?
                        WHERE message_id=? AND kind='supervisor_instruction'
                          AND processing_state='pending'
                        """,
                        (error_json, str(claim.message_id)),
                    )
                    if message_cursor.rowcount != 1:
                        raise _claim_lost()
                    expected_message = (
                        "supervisor_instruction",
                        "blocked",
                        error_json,
                    )
                    message_state = connection.execute(
                        """
                        SELECT kind, processing_state, processing_error_json
                        FROM messages WHERE message_id=?
                        """,
                        (str(claim.message_id),),
                    ).fetchone()
                if message_state is None or tuple(message_state) != expected_message:
                    raise ContextFlowError(
                        "PARSE_JOB_WRITE_FAILED",
                        "finalize did not leave the source Message in the expected state",
                        details={"operation": "verify_message_finalize"},
                    )

                stored_row = connection.execute(
                    "SELECT * FROM message_parse_jobs WHERE message_id=?",
                    (str(claim.message_id),),
                ).fetchone()
                if stored_row is None:
                    raise ContextFlowError(
                        "PARSE_JOB_WRITE_FAILED",
                        "finalize did not retain the expected parse job",
                        details={"operation": "finalize"},
                    )
                stored = _stored_from_row(stored_row)
                stored_job = _decode_job(stored_row)
                if (
                    stored.result != result
                    or stored.completed_at_ms != now
                    or stored_job.claim_generation != claim.claim_generation
                    or stored_job.attempt_count != owned["attempt_count"]
                    or stored_job.last_attempt_generation != owned["last_attempt_generation"]
                    or stored_job.max_attempts != owned["max_attempts"]
                    or stored_job.lease_owner is not None
                    or stored_job.claim_request_id is not None
                    or stored_job.lease_expires_at_ms is not None
                    or stored_job.next_attempt_at_ms is not None
                ):
                    raise ContextFlowError(
                        "PARSE_JOB_WRITE_FAILED",
                        "finalize did not store the expected ParseResult",
                        details={"operation": "verify_finalize"},
                    )
                connection.execute("COMMIT")
                return stored
            except sqlite3.Error as exc:
                rollback_error = self._rollback()
                raise _database_error(exc, "finalize", rollback_error) from exc
            except BaseException:
                self._rollback()
                raise


def _argument(field: str, reason: str) -> ContextFlowError:
    return ContextFlowError(
        "INVALID_ARGUMENT",
        f"invalid {field}: {reason}",
        details={"field": field},
    )


def _integer(value: object, field: str, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise _argument(field, f"must be an integer >= {minimum}")
    return value


def _duration(value: object, field: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _argument(field, "must be a finite number")
    if value < 0 or (value == 0 and not allow_zero):
        raise _argument(field, "must be nonnegative" if allow_zero else "must be positive")
    return float(value)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise _argument(field, "must be a non-empty string of at most 256 characters")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise _argument(field, "must be valid UTF-8 Unicode") from exc
    return value


def _message_id(value: object) -> MessageId:
    if not isinstance(value, MessageId):
        raise _argument("message_id", "must be a MessageId")
    return value


def _claim(value: object) -> ParseClaim:
    if not isinstance(value, ParseClaim):
        raise _argument("claim", "must be a ParseClaim")
    _message_id(value.message_id)
    _text(value.owner, "claim.owner")
    _text(value.claim_request_id, "claim.claim_request_id")
    _integer(value.claim_generation, "claim.claim_generation", minimum=1)
    _integer(value.lease_expires_at_ms, "claim.lease_expires_at_ms", minimum=0)
    _integer(value.attempt_count, "claim.attempt_count", minimum=0)
    _integer(value.max_attempts, "claim.max_attempts", minimum=1)
    return value


def _error(value: object) -> ErrorInfo:
    if not isinstance(value, ErrorInfo):
        raise _argument("error", "must be an ErrorInfo")
    return value


def _claim_lost() -> ContextFlowError:
    return ContextFlowError(
        "PARSE_CLAIM_LOST",
        "parse claim is missing, expired, superseded, or no longer eligible",
    )


def _corrupt(reason: str) -> ContextFlowError:
    return ContextFlowError(
        "PARSE_JOB_CORRUPT",
        "stored ParseJob violates its contract",
        details={"reason": reason},
    )


def _database_error(
    error: sqlite3.Error,
    operation: str,
    rollback_error: sqlite3.Error | None = None,
) -> ContextFlowError:
    busy = rollback_error is None and isinstance(error, sqlite3.OperationalError) and any(
        word in str(error).lower() for word in ("locked", "busy")
    )
    details: dict[str, Any] = {
        "operation": operation,
        "sqlite_error": type(error).__name__,
    }
    if rollback_error is not None:
        details["rollback_error"] = type(rollback_error).__name__
    return ContextFlowError(
        "PARSE_JOB_BUSY" if busy else "PARSE_JOB_WRITE_FAILED",
        "parse job database is busy" if busy else "parse job database operation failed",
        retryable=busy,
        details=details,
    )


def _database_now(connection: sqlite3.Connection) -> int:
    value = connection.execute(f"SELECT {_NOW_MS_SQL}").fetchone()[0]
    if type(value) is not int or value < 0:
        raise _corrupt("database clock did not return epoch milliseconds")
    return value


def _canonical_error(error: ErrorInfo) -> str:
    try:
        text = json.dumps(
            error.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        text.encode("utf-8")
        return text
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise _argument("error", "must contain canonical UTF-8 JSON values") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


def _decode_error(text: object) -> ErrorInfo:
    if not isinstance(text, str):
        raise _corrupt("last_error_json must be text")
    try:
        text.encode("utf-8")
        raw = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite number {value}")
            ),
        )
        if not isinstance(raw, dict) or set(raw) != {
            "code", "message", "retryable", "details"
        }:
            raise ValueError("wrong ErrorInfo fields")
        return ErrorInfo(**raw)
    except (ValueError, TypeError, ContextFlowError, UnicodeError, RecursionError) as exc:
        raise _corrupt("last_error_json violates ErrorInfo") from exc


def _claim_from_row(row: sqlite3.Row) -> ParseClaim:
    try:
        if row["job_state"] != JobState.PROCESSING.value:
            raise ValueError("claim row is not processing")
        return ParseClaim(
            message_id=MessageId(row["message_id"]),
            owner=_text(row["lease_owner"], "lease_owner"),
            claim_request_id=_text(row["claim_request_id"], "claim_request_id"),
            claim_generation=_integer(
                row["claim_generation"], "claim_generation", minimum=1
            ),
            lease_expires_at_ms=_integer(
                row["lease_expires_at_ms"], "lease_expires_at_ms", minimum=0
            ),
            attempt_count=_integer(row["attempt_count"], "attempt_count", minimum=0),
            max_attempts=_integer(row["max_attempts"], "max_attempts", minimum=1),
        )
    except (ContextFlowError, TypeError, ValueError, KeyError) as exc:
        raise _corrupt("processing row cannot form a ParseClaim") from exc


def _decode_job(row: sqlite3.Row) -> ParseJob:
    try:
        state = JobState(row["job_state"])
        last_error = (
            _decode_error(row["last_error_json"])
            if row["last_error_json"] is not None
            else None
        )
        formal_result = None
        if state is JobState.COMPLETED:
            formal_result = decode_parse_result(
                EncodedParseResult(
                    status=row["result_status"],
                    instructions_json=row["instructions_json"],
                    error_json=row["result_error_json"],
                    fingerprint=row["intent_fingerprint"],
                )
            )
        elif any(
            row[name] is not None
            for name in (
                "result_status",
                "instructions_json",
                "result_error_json",
                "intent_fingerprint",
                "completed_at_ms",
            )
        ):
            raise ValueError("non-completed job has formal result fields")
        job = ParseJob(
            message_id=MessageId(row["message_id"]),
            state=state,
            claim_generation=_integer(
                row["claim_generation"], "claim_generation", minimum=0
            ),
            lease_owner=row["lease_owner"],
            claim_request_id=row["claim_request_id"],
            lease_expires_at_ms=row["lease_expires_at_ms"],
            attempt_count=_integer(row["attempt_count"], "attempt_count", minimum=0),
            last_attempt_generation=row["last_attempt_generation"],
            max_attempts=_integer(row["max_attempts"], "max_attempts", minimum=1),
            next_attempt_at_ms=row["next_attempt_at_ms"],
            last_error=last_error,
            formal_result=formal_result,
            created_at_ms=_integer(row["created_at_ms"], "created_at_ms", minimum=0),
            updated_at_ms=_integer(row["updated_at_ms"], "updated_at_ms", minimum=0),
            completed_at_ms=row["completed_at_ms"],
        )
        _validate_decoded_job(job)
        return job
    except (ContextFlowError, TypeError, ValueError, KeyError) as exc:
        if isinstance(exc, ContextFlowError) and exc.code == "PARSE_JOB_CORRUPT":
            raise
        raise _corrupt("row cannot form a ParseJob") from exc


def _optional_epoch(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _integer(value, field, minimum=0)


def _validate_decoded_job(job: ParseJob) -> None:
    _optional_epoch(job.lease_expires_at_ms, "lease_expires_at_ms")
    _optional_epoch(job.next_attempt_at_ms, "next_attempt_at_ms")
    _optional_epoch(job.completed_at_ms, "completed_at_ms")
    if job.attempt_count > job.max_attempts or job.attempt_count > job.claim_generation:
        raise _corrupt("attempt count exceeds budget or generation")
    if (job.attempt_count == 0) != (job.last_attempt_generation is None):
        raise _corrupt("attempt count and last generation are inconsistent")
    if job.last_attempt_generation is not None and (
        type(job.last_attempt_generation) is not int
        or job.last_attempt_generation < 1
        or job.last_attempt_generation > job.claim_generation
    ):
        raise _corrupt("last attempt generation is invalid")
    has_claim = (
        job.lease_owner is not None,
        job.claim_request_id is not None,
        job.lease_expires_at_ms is not None,
    )
    if job.state is JobState.READY:
        valid = (
            job.claim_generation == 0
            and job.attempt_count == 0
            and not any(has_claim)
            and job.next_attempt_at_ms is None
            and job.formal_result is None
            and job.completed_at_ms is None
        )
    elif job.state is JobState.PROCESSING:
        valid = (
            job.claim_generation > 0
            and all(has_claim)
            and job.next_attempt_at_ms is None
            and job.formal_result is None
            and job.completed_at_ms is None
        )
    elif job.state is JobState.RETRY_WAIT:
        valid = (
            job.claim_generation > 0
            and not any(has_claim)
            and job.next_attempt_at_ms is not None
            and job.formal_result is None
            and job.completed_at_ms is None
        )
    else:
        valid = (
            job.claim_generation > 0
            and not any(has_claim)
            and job.next_attempt_at_ms is None
            and job.formal_result is not None
            and job.completed_at_ms is not None
        )
    if not valid:
        raise _corrupt("job state fields are inconsistent")


def _stored_from_row(row: sqlite3.Row) -> StoredParseResult:
    job = _decode_job(row)
    if (
        job.state is not JobState.COMPLETED
        or job.formal_result is None
        or job.completed_at_ms is None
    ):
        raise _corrupt("row is not a completed ParseResult")
    return StoredParseResult(
        message_id=job.message_id,
        result=job.formal_result,
        completed_at_ms=job.completed_at_ms,
    )


def _select_owned(
    connection: sqlite3.Connection, claim: ParseClaim, now: int
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT j.*
        FROM message_parse_jobs AS j
        JOIN messages AS m ON m.message_id=j.message_id
        WHERE j.message_id=? AND j.job_state='processing'
          AND j.lease_owner=? AND j.claim_request_id=?
          AND j.claim_generation=? AND j.lease_expires_at_ms>?
          AND m.kind='supervisor_instruction'
          AND m.processing_state='pending'
        """,
        (
            str(claim.message_id),
            claim.owner,
            claim.claim_request_id,
            claim.claim_generation,
            now,
        ),
    ).fetchone()
