"""Runtime-distinct string IDs; no ID generation or identity registration."""

from __future__ import annotations

import re

from ..errors import ContextFlowError
from ..submission import ENDPOINT_ID_RE, MAX_ID_LENGTH, SUBMISSION_ID_RE


class _Identifier(str):
    pattern: re.Pattern[str] | None = None
    max_length: int | None = MAX_ID_LENGTH

    def __new__(cls, value: str):
        if isinstance(value, _Identifier) and type(value) is not cls:
            raise ContextFlowError(
                "INVALID_CONTRACT",
                f"{cls.__name__} cannot reinterpret {type(value).__name__}",
                details={"field": cls.__name__},
            )
        if not isinstance(value, str):
            raise ContextFlowError(
                "INVALID_CONTRACT",
                f"{cls.__name__} must be a non-empty string",
                details={"field": cls.__name__},
            )
        # Normalize the underlying text before validating or constructing. A
        # string Enum can override __str__ with its display name.
        value = str.__str__(value)
        if not value.strip():
            raise ContextFlowError(
                "INVALID_CONTRACT",
                f"{cls.__name__} must be a non-empty string",
                details={"field": cls.__name__},
            )
        if cls.max_length is not None and len(value) > cls.max_length:
            raise ContextFlowError(
                "INVALID_CONTRACT",
                f"{cls.__name__} exceeds {cls.max_length} characters",
                details={"field": cls.__name__},
            )
        # Reuse match() for the existing Adapter's two ID patterns, rather than
        # silently tightening its v1 wire validation.
        if cls.pattern is not None and cls.pattern.match(value) is None:
            raise ContextFlowError(
                "INVALID_CONTRACT",
                f"invalid {cls.__name__} namespace or format",
                details={"field": cls.__name__},
            )
        return str.__new__(cls, value)


class MessageId(_Identifier):
    pattern = re.compile(r"^cfmsg_[0-9a-f]{32}\Z")


class TaskId(_Identifier):
    pattern = re.compile(r"^cftask_[0-9a-f]{32}\Z")


class DeliveryId(_Identifier):
    pattern = re.compile(r"^cfdel_[0-9a-f]{32}\Z")


class EndpointId(_Identifier):
    pattern = ENDPOINT_ID_RE
    max_length = None  # Existing Envelope v1 does not cap this field.


class SubmissionId(_Identifier):
    pattern = SUBMISSION_ID_RE
    max_length = None  # Existing Envelope v1 does not cap this field.


class NativeSessionId(_Identifier):
    pass


class NativeMessageId(_Identifier):
    pass


class AgentId(_Identifier):
    pass


class ReportId(_Identifier):
    pass
