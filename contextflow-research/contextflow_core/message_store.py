"""SQLite persistence and passive processing markers for the MessageStore Port."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import threading
import uuid
from typing import Any

from .contracts import (
    AcceptResult, AcceptStatus, AgentId, AgentReportEnvelope, AgentSubmission,
    EndpointIdentity, EndpointIdentityReader, ErrorInfo, Message, MessageId,
    MessageKind, MessageSubmission, ProcessingState, SubmissionId,
    SupervisorEnvelope, SupervisorSubmission,
)
from .errors import ContextFlowError
from .database_schema import (
    MESSAGE_COLUMNS as _COLUMNS,
    ensure_schema_v2,
)
from .registry import default_registry_path
from .submission import canonical_envelope, envelope_fingerprint

def default_message_store_path() -> Path:
    configured = os.environ.get('CONTEXTFLOW_MESSAGE_STORE')
    return Path(configured).expanduser() if configured else default_registry_path().with_name('messages.sqlite3')


def _argument(field: str, reason: str) -> ContextFlowError:
    return ContextFlowError('INVALID_ARGUMENT', f'invalid {field}: {reason}', details={'field': field})


def _duration(value: float, field: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _argument(field, 'must be a finite number')
    if value < 0 or (value == 0 and not allow_zero):
        raise _argument(field, 'must be positive' if not allow_zero else 'must be nonnegative')
    return float(value)


def _database_error(error: sqlite3.Error, operation: str, rollback_error: sqlite3.Error | None = None) -> ContextFlowError:
    busy = rollback_error is None and isinstance(error, sqlite3.OperationalError) and any(word in str(error).lower() for word in ('locked', 'busy'))
    details = {'operation': operation, 'sqlite_error': type(error).__name__}
    if rollback_error is not None:
        details['rollback_error'] = type(rollback_error).__name__
    return ContextFlowError(
        'MESSAGE_STORE_BUSY' if busy else 'MESSAGE_STORE_WRITE_FAILED',
        'message database is busy' if busy else 'message database operation failed',
        retryable=busy,
        details=details,
    )


@dataclass(frozen=True)
class _Prepared:
    kind: MessageKind
    envelope: SupervisorEnvelope | AgentReportEnvelope
    envelope_json: str
    fingerprint: str
    submission_id: str | None = None
    source_agent_id: AgentId | None = None
    report_id: str | None = None
    endpoint_id: str | None = None
    native_session_id: str | None = None
    native_message_id: str | None = None


class SQLiteMessageStore:
    """File-backed implementation; AgentSubmission is trusted internal input from D.

    No Agent authentication, Task lookup, parsing, dispatch or report return occurs
    here. Each connection is serialized; SQLite transactions serialize separate
    connections. Cancelling an await does not cancel an already running SQLite
    worker: a caller without the result must treat acceptance as unconfirmed.
    """

    def __init__(
        self, path: str | Path, identities: EndpointIdentityReader,
        *, busy_timeout: float = 5.0, identity_timeout: float = 5.0,
    ) -> None:
        if str(path) == ':memory:' or str(path).startswith('file:'):
            raise _argument('path', 'must name a persistent database file')
        self.path = Path(path).expanduser()
        self.identities = identities
        self.identity_timeout = _duration(identity_timeout, 'identity_timeout')
        busy_timeout = _duration(busy_timeout, 'busy_timeout', allow_zero=True)
        self._lock = threading.Lock()
        self._closed = False
        connection: sqlite3.Connection | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=busy_timeout, isolation_level=None, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            self._connection = connection
            self._initialize()
        except (OSError, sqlite3.Error) as exc:
            if connection is not None:
                connection.close()
            if isinstance(exc, sqlite3.Error):
                raise _database_error(exc, 'initialize') from exc
            raise ContextFlowError('MESSAGE_STORE_OPEN_FAILED', 'cannot open message database', details={'operation': 'initialize'}) from exc
        except BaseException:
            if connection is not None:
                connection.close()
            raise

    def _initialize(self) -> None:
        ensure_schema_v2(self._connection)

    def _ensure_open(self) -> None:
        if self._closed:
            raise ContextFlowError('MESSAGE_STORE_CLOSED', 'message database is closed')

    def _rollback(self) -> sqlite3.Error | None:
        if self._closed:
            return None
        if self._connection.in_transaction:
            try:
                self._connection.execute('ROLLBACK')
            except sqlite3.Error as exc:
                self._closed = True
                try:
                    self._connection.close()
                except sqlite3.Error:
                    pass
                return exc
        return None

    async def close(self) -> None:
        await asyncio.to_thread(self._close)

    def _close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._connection.close()

    def _prepare(self, submission: MessageSubmission) -> _Prepared:
        if isinstance(submission, SupervisorSubmission):
            raw = submission.envelope.to_dict() if isinstance(submission.envelope, SupervisorEnvelope) else submission.envelope
            envelope = SupervisorEnvelope.from_dict(raw)
            document = envelope.to_dict()
            return _Prepared(
                kind=MessageKind.SUPERVISOR_INSTRUCTION, envelope=envelope,
                envelope_json=canonical_envelope(document), fingerprint=envelope_fingerprint(document),
                submission_id=str(envelope.submission_id), endpoint_id=str(envelope.source.endpoint_id),
                native_session_id=str(envelope.source.native_session_id), native_message_id=str(envelope.source.message_id),
            )
        if isinstance(submission, AgentSubmission):
            document = submission.envelope.to_dict()
            return _Prepared(
                kind=MessageKind.AGENT_REPORT, envelope=submission.envelope,
                envelope_json=canonical_envelope(document), fingerprint=envelope_fingerprint(document),
                source_agent_id=submission.source_agent_id, report_id=str(submission.envelope.report_id),
            )
        raise _argument('submission', 'must be a MessageSubmission')

    async def _validate_source(self, envelope: SupervisorEnvelope) -> None:
        try:
            identity = await asyncio.wait_for(self.identities.get_identity(envelope.source.endpoint_id), self.identity_timeout)
        except asyncio.TimeoutError as exc:
            raise ContextFlowError('SOURCE_LOOKUP_TIMEOUT', 'endpoint identity lookup timed out', retryable=True) from exc
        if identity is None:
            raise ContextFlowError('ENDPOINT_NOT_FOUND', 'source Endpoint is not registered')
        if not isinstance(identity, EndpointIdentity):
            raise ContextFlowError('SOURCE_IDENTITY_INVALID', 'identity reader returned an invalid identity')
        if identity.endpoint_id != envelope.source.endpoint_id:
            raise ContextFlowError('SOURCE_ENDPOINT_MISMATCH', 'identity reader returned a different Endpoint')
        if identity.provider != envelope.source.adapter:
            raise ContextFlowError('SOURCE_PROVIDER_MISMATCH', 'source adapter does not match registered provider')
        if identity.native_session_id != envelope.source.native_session_id:
            raise ContextFlowError('SOURCE_SESSION_MISMATCH', 'source session does not match registered identity')

    async def accept_message(self, submission: MessageSubmission) -> AcceptResult:
        try:
            self._ensure_open()
            prepared = self._prepare(submission)
            if isinstance(prepared.envelope, SupervisorEnvelope):
                await self._validate_source(prepared.envelope)
            return await asyncio.to_thread(self._accept, prepared)
        except UnicodeError:
            return AcceptResult(status=AcceptStatus.REJECTED, error=ErrorInfo(code='INVALID_ENVELOPE_ENCODING', message='Envelope cannot be represented as UTF-8'))
        except ContextFlowError as exc:
            return AcceptResult(status=AcceptStatus.REJECTED, error=ErrorInfo.from_error(exc))

    def _accept(self, prepared: _Prepared) -> AcceptResult:
        with self._lock:
            self._ensure_open()
            connection = self._connection
            try:
                connection.execute('BEGIN IMMEDIATE')
                if prepared.kind is MessageKind.SUPERVISOR_INSTRUCTION:
                    row = connection.execute("SELECT * FROM messages WHERE kind='supervisor_instruction' AND submission_id=?", (prepared.submission_id,)).fetchone()
                else:
                    row = connection.execute("SELECT * FROM messages WHERE kind='agent_report' AND source_agent_id=? AND report_id=?", (str(prepared.source_agent_id), prepared.report_id)).fetchone()
                if row is not None:
                    existing = self._decode(row)
                    if row['fingerprint'] != prepared.fingerprint or canonical_envelope(existing.envelope.to_dict()) != prepared.envelope_json:
                        connection.execute('ROLLBACK')
                        supervisor = prepared.kind is MessageKind.SUPERVISOR_INSTRUCTION
                        return AcceptResult(status=AcceptStatus.CONFLICT, error=ErrorInfo(
                            code='SUBMISSION_CONFLICT' if supervisor else 'REPORT_CONFLICT',
                            message='submission identity already accepted with a different Envelope',
                            details={'stored_fingerprint': row['fingerprint'], 'incoming_fingerprint': prepared.fingerprint},
                        ))
                    connection.execute('COMMIT')
                    return AcceptResult(status=AcceptStatus.ACCEPTED, message_id=existing.message_id, duplicate=True)
                message_id = MessageId('cfmsg_' + uuid.uuid4().hex)
                received_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                message = Message(message_id=message_id, kind=prepared.kind, envelope=prepared.envelope, received_at=received_at, source_agent_id=prepared.source_agent_id)
                values = (
                    str(message.message_id), message.kind.value, prepared.submission_id,
                    str(prepared.source_agent_id) if prepared.source_agent_id is not None else None,
                    prepared.report_id, prepared.endpoint_id, prepared.native_session_id, prepared.native_message_id,
                    prepared.envelope_json, prepared.fingerprint, received_at, ProcessingState.PENDING.value, None,
                )
                cursor = connection.execute(f"INSERT INTO messages ({','.join(_COLUMNS)}) VALUES ({','.join('?' for _ in _COLUMNS)})", values)
                stored = connection.execute('SELECT * FROM messages WHERE message_id=?', (str(message_id),)).fetchone()
                if cursor.rowcount != 1 or stored is None or self._decode(stored) != message:
                    raise ContextFlowError('MESSAGE_STORE_WRITE_FAILED', 'transaction did not create the expected Message', details={'operation': 'verify_insert'})
                connection.execute('COMMIT')
                return AcceptResult(status=AcceptStatus.ACCEPTED, message_id=message_id)
            except sqlite3.Error as exc:
                rollback_error = self._rollback()
                raise _database_error(exc, 'accept', rollback_error) from exc
            except BaseException:
                self._rollback()
                raise

    def _decode(self, row: sqlite3.Row) -> Message:
        try:
            if not isinstance(row['envelope_json'], str):
                raise ValueError('Envelope must be text')
            raw = json.loads(row['envelope_json'])
            kind = MessageKind(row['kind'])
            if kind is MessageKind.SUPERVISOR_INSTRUCTION:
                envelope = SupervisorEnvelope.from_dict(raw)
                source_agent_id = None
                expected = (str(envelope.submission_id), None, None, str(envelope.source.endpoint_id), str(envelope.source.native_session_id), str(envelope.source.message_id))
            else:
                envelope = AgentReportEnvelope.from_dict(raw)
                source_agent_id = AgentId(row['source_agent_id'])
                expected = (None, str(source_agent_id), str(envelope.report_id), None, None, None)
            actual = tuple(row[name] for name in ('submission_id', 'source_agent_id', 'report_id', 'endpoint_id', 'native_session_id', 'native_message_id'))
            if actual != expected or envelope_fingerprint(envelope.to_dict()) != row['fingerprint']:
                raise ValueError('Stored identity or fingerprint mismatch')
            error = None
            if row['processing_error_json'] is not None:
                error_raw = json.loads(row['processing_error_json'])
                if not isinstance(error_raw, dict) or set(error_raw) != {'code', 'message', 'retryable', 'details'}:
                    raise ValueError('Invalid processing error')
                error = ErrorInfo(**error_raw)
            return Message(
                message_id=MessageId(row['message_id']), kind=kind, envelope=envelope,
                received_at=row['received_at'], source_agent_id=source_agent_id,
                processing_state=ProcessingState(row['processing_state']), processing_error=error,
            )
        except (ContextFlowError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
            raise ContextFlowError('MESSAGE_STORE_CORRUPT', 'stored Message violates its contract', details={'operation': 'decode'}) from exc

    async def get_message(self, message_id: MessageId) -> Message | None:
        if not isinstance(message_id, MessageId):
            raise _argument('message_id', 'must be a MessageId')
        result = await asyncio.to_thread(self._query, 'SELECT * FROM messages WHERE message_id=?', (str(message_id),))
        return result[0] if result else None

    async def get_message_by_submission_id(self, submission_id: SubmissionId) -> Message | None:
        if not isinstance(submission_id, SubmissionId):
            raise _argument('submission_id', 'must be a SubmissionId')
        result = await asyncio.to_thread(self._query, "SELECT * FROM messages WHERE kind='supervisor_instruction' AND submission_id=?", (str(submission_id),))
        return result[0] if result else None

    async def list_pending(self, kind: MessageKind, limit: int) -> tuple[Message, ...]:
        if not isinstance(kind, MessageKind):
            raise _argument('kind', 'must be a MessageKind')
        if type(limit) is not int or limit < 0:
            raise _argument('limit', 'must be a nonnegative integer')
        return await asyncio.to_thread(self._query, "SELECT * FROM messages WHERE kind=? AND processing_state='pending' ORDER BY received_at, message_id LIMIT ?", (kind.value, limit))

    def _query(self, sql: str, parameters: tuple[Any, ...]) -> tuple[Message, ...]:
        with self._lock:
            self._ensure_open()
            try:
                return tuple(self._decode(row) for row in self._connection.execute(sql, parameters).fetchall())
            except sqlite3.Error as exc:
                raise _database_error(exc, 'query') from exc

    async def mark_processed(self, message_id: MessageId) -> None:
        await self._mark(message_id, ProcessingState.PROCESSED, None)

    async def mark_blocked(self, message_id: MessageId, error: ErrorInfo) -> None:
        if not isinstance(error, ErrorInfo):
            raise _argument('error', 'must be an ErrorInfo')
        await self._mark(message_id, ProcessingState.BLOCKED, error)

    async def _mark(self, message_id: MessageId, state: ProcessingState, error: ErrorInfo | None) -> None:
        if not isinstance(message_id, MessageId):
            raise _argument('message_id', 'must be a MessageId')
        error_json = canonical_envelope(error.to_dict()) if error is not None else None
        if error_json is not None:
            try:
                error_json.encode('utf-8')
            except UnicodeError as exc:
                raise _argument('error', 'must be UTF-8 encodable') from exc
        await asyncio.to_thread(self._mark_sync, message_id, state, error_json)

    def _mark_sync(self, message_id: MessageId, state: ProcessingState, error_json: str | None) -> None:
        with self._lock:
            self._ensure_open()
            try:
                self._connection.execute('BEGIN IMMEDIATE')
                row = self._connection.execute('SELECT * FROM messages WHERE message_id=?', (str(message_id),)).fetchone()
                if row is None:
                    raise ContextFlowError('MESSAGE_NOT_FOUND', 'cannot mark a missing Message')
                expected = replace(
                    self._decode(row), processing_state=state,
                    processing_error=ErrorInfo(**json.loads(error_json)) if error_json is not None else None,
                )
                cursor = self._connection.execute('UPDATE messages SET processing_state=?, processing_error_json=? WHERE message_id=?', (state.value, error_json, str(message_id)))
                stored = self._connection.execute('SELECT * FROM messages WHERE message_id=?', (str(message_id),)).fetchone()
                if cursor.rowcount != 1 or stored is None or self._decode(stored) != expected:
                    raise ContextFlowError('MESSAGE_STORE_WRITE_FAILED', 'transaction did not update the expected Message', details={'operation': 'verify_mark'})
                self._connection.execute('COMMIT')
            except sqlite3.Error as exc:
                rollback_error = self._rollback()
                raise _database_error(exc, 'mark', rollback_error) from exc
            except BaseException:
                self._rollback()
                raise
