"""Authorized Supervisor binding and read-only ingestion/Parser status views."""

from __future__ import annotations

import asyncio
import unicodedata
from typing import Any

from . import contracts as c
from .errors import ContextFlowError
from .message_store import SQLiteMessageStore
from .parse_jobs import JobState, SQLiteParseJobRepository
from .registry import EndpointRegistry


_CONTROL_ERROR = {
    "code": "CORE_CONTROL_ERROR",
    "message": "Supervisor control operation failed",
}
_INVALID_ERROR = {
    "code": "INVALID_CONTROL_REQUEST",
    "message": "invalid Supervisor control request",
}
_ALLOWED_SESSION_KINDS = frozenset(("chat", "project"))
_MAX_CONTROL_TEXT = 256
_PUBLIC_PARSE_ERRORS = {
    c.ParseStatus.UNRESOLVED: (
        "PARSER_UNRESOLVED",
        "Parser could not determine a complete, trusted instruction set",
    ),
    c.ParseStatus.FAILED: (
        "PARSER_FAILED",
        "Parser could not complete instruction parsing",
    ),
}


class SupervisorControlService:
    """Narrow Core-owned Supervisor identity and status operations.

    Transport authentication remains the Bridge/Receiver's responsibility.  This
    service additionally enforces Endpoint and native-message ownership before
    disclosing whether a submission exists.
    """

    def __init__(
        self,
        registry: EndpointRegistry,
        messages: SQLiteMessageStore,
        parse_jobs: SQLiteParseJobRepository,
    ) -> None:
        if not isinstance(registry, EndpointRegistry):
            raise _invalid()
        if not callable(getattr(messages, "get_message_by_submission_id", None)):
            raise _invalid()
        if not callable(getattr(parse_jobs, "get_job", None)):
            raise _invalid()
        self.registry = registry
        self.registry_path = registry.path
        self._registry_lock = asyncio.Lock()
        self.messages = messages
        self.parse_jobs = parse_jobs

    async def handle(self, action: object, params: object) -> dict[str, Any]:
        """Return a sanitized transport-neutral result; cancellation propagates."""

        try:
            if action == "ping_core":
                _exact(params, ())
                data = {"available": True}
            elif action == "bind_supervisor":
                data = await self.bind_supervisor(params)
            elif action == "get_submission_status":
                data = await self.get_submission_status(params)
            else:
                raise _invalid()
            return {"success": True, "data": data}
        except asyncio.CancelledError:
            raise
        except ContextFlowError as exc:
            error = _INVALID_ERROR if exc.code == "INVALID_CONTROL_REQUEST" else _CONTROL_ERROR
            return {"success": False, "error": dict(error)}
        except Exception:
            return {"success": False, "error": dict(_CONTROL_ERROR)}

    async def bind_supervisor(self, params: object) -> dict[str, Any]:
        document = _exact(params, ("native_session_id", "session_kind"))
        native_session_id = _text(document["native_session_id"])
        session_kind = _text(document["session_kind"])
        if session_kind not in _ALLOWED_SESSION_KINDS:
            raise _invalid()

        binding, created = await self._with_registry(
            lambda registry: registry.register(
                "chatgpt_web", native_session_id, session_kind
            )
        )
        return {
            "endpoint_id": binding.endpoint_id,
            "native_session_id": binding.native_session_id,
            "session_kind": binding.session_kind,
            "created": created,
        }

    async def get_submission_status(self, params: object) -> dict[str, Any]:
        document = _exact(
            params,
            (
                "submission_id",
                "endpoint_id",
                "native_session_id",
                "native_message_id",
            ),
        )
        submission_text = _text(document["submission_id"])
        endpoint_text = _text(document["endpoint_id"])
        native_session_id = _text(document["native_session_id"])
        native_message_id = _text(document["native_message_id"])
        try:
            submission_id = c.SubmissionId(submission_text)
            endpoint_id = c.EndpointId(endpoint_text)
        except ContextFlowError as exc:
            raise _invalid() from exc

        binding = await self._with_registry(
            lambda registry: registry.get(str(endpoint_id))
        )
        if (
            binding is None
            or binding.provider != "chatgpt_web"
            or binding.native_session_id != native_session_id
        ):
            return {"found": False}

        message = await self.messages.get_message_by_submission_id(submission_id)
        if message is None or message.kind is not c.MessageKind.SUPERVISOR_INSTRUCTION:
            return {"found": False}
        envelope = message.envelope
        source = envelope.source
        if (
            str(source.endpoint_id) != str(endpoint_id)
            or str(source.native_session_id) != native_session_id
            or str(source.message_id) != native_message_id
        ):
            return {"found": False}

        job = await self.parse_jobs.get_job(message.message_id)
        parser = _public_parser(job)
        return {
            "found": True,
            "submission_id": str(envelope.submission_id),
            "message_id": str(message.message_id),
            "core_status": "accepted",
            "parser": parser,
        }

    async def _with_registry(self, operation):
        """Serialize fresh Registry snapshots and settle any cancelled thread."""

        async with self._registry_lock:
            task = asyncio.create_task(
                asyncio.to_thread(self._run_registry_operation, operation)
            )
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                try:
                    await task
                except BaseException:
                    pass
                raise

    def _run_registry_operation(self, operation):
        registry = EndpointRegistry(self.registry_path)
        return operation(registry)


def _public_parser(job: object) -> dict[str, Any]:
    public: dict[str, Any] = {
        "status": "waiting",
        "instruction_count": 0,
        "target_agents": [],
        "error": None,
        "completed_at_ms": None,
    }
    if job is None or job.state is JobState.READY:
        return public
    if job.state is JobState.PROCESSING:
        public["status"] = "processing"
        return public
    if job.state is JobState.RETRY_WAIT:
        public["status"] = "retry_wait"
        return public
    if job.state is not JobState.COMPLETED or job.formal_result is None:
        raise ContextFlowError(
            "PARSE_JOB_CORRUPT", "stored ParseJob violates its contract"
        )

    result = job.formal_result
    public["status"] = result.status.value
    public["completed_at_ms"] = job.completed_at_ms
    if result.status is c.ParseStatus.PARSED:
        public["instruction_count"] = len(result.instructions)
        public["target_agents"] = [item.target_agent for item in result.instructions]
    else:
        public["error"] = _public_error(result.status, result.error)
    return public


def _public_error(
    status: c.ParseStatus, error: c.ErrorInfo | None
) -> dict[str, Any]:
    if error is None or status not in _PUBLIC_PARSE_ERRORS:
        raise ContextFlowError(
            "PARSE_JOB_CORRUPT", "stored ParseResult is missing an error"
        )
    code, message = _PUBLIC_PARSE_ERRORS[status]
    return {
        "code": code,
        "message": message,
        "retryable": error.retryable,
    }


def _exact(value: object, fields: tuple[str, ...]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise _invalid()
    return value


def _text(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_CONTROL_TEXT
        or value != value.strip()
    ):
        raise _invalid()
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise _invalid() from exc
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise _invalid()
    return value


def _invalid() -> ContextFlowError:
    return ContextFlowError(
        "INVALID_CONTROL_REQUEST", "invalid Supervisor control request"
    )
