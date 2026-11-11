"""Stable error types for the Agent Runtime adapter layer.

These errors are adapter-local. They deliberately do not reuse the Core
`ContextFlowError` so that the adapter layer can be tested and embedded without
importing Core business modules. The Dispatcher maps them to Core error codes.
"""

from __future__ import annotations

from typing import Any


class AdapterError(Exception):
    """Base error for every adapter failure.

    ``retryable`` describes whether the *same* call may be attempted again. It
    never authorises re-delivery of a task: Core owns that decision.
    """

    code = "ADAPTER_ERROR"

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{self.code}: {message}")
        self.message = message
        self.retryable = retryable
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AdapterError":
        code = raw.get("code", "ADAPTER_ERROR")
        error_cls = _BY_CODE.get(code, AdapterError)
        return error_cls(
            str(raw.get("message", "")),
            retryable=bool(raw.get("retryable", False)),
            details=dict(raw.get("details") or {}),
        )


class InvalidRequest(AdapterError):
    code = "ADAPTER_INVALID_REQUEST"


class UnsupportedOperation(AdapterError):
    code = "ADAPTER_UNSUPPORTED"


class RuntimeUnavailable(AdapterError):
    code = "ADAPTER_RUNTIME_UNAVAILABLE"


class SessionUnavailable(AdapterError):
    code = "ADAPTER_SESSION_UNAVAILABLE"


class ExecutionNotFound(AdapterError):
    code = "ADAPTER_EXECUTION_NOT_FOUND"


class ConcurrencyLimit(AdapterError):
    code = "ADAPTER_CONCURRENCY_LIMIT"


class IdempotencyConflict(AdapterError):
    code = "ADAPTER_IDEMPOTENCY_CONFLICT"


class ProtocolError(AdapterError):
    code = "ADAPTER_PROTOCOL_ERROR"


_BY_CODE = {
    cls.code: cls
    for cls in (
        AdapterError,
        InvalidRequest,
        UnsupportedOperation,
        RuntimeUnavailable,
        SessionUnavailable,
        ExecutionNotFound,
        ConcurrencyLimit,
        IdempotencyConflict,
        ProtocolError,
    )
}
