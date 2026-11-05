"""Data contracts exposed by the ContextFlow Endpoint service."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EndpointState(str, Enum):
    AVAILABLE = "available"
    OFFLINE = "offline"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class EndpointBinding:
    endpoint_id: str
    provider: str
    native_session_id: str
    session_kind: str | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint_id": self.endpoint_id,
            "provider": self.provider,
            "native_session_id": self.native_session_id,
            "session_kind": self.session_kind,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EndpointBinding":
        return cls(
            endpoint_id=data["endpoint_id"],
            provider=data["provider"],
            native_session_id=data["native_session_id"],
            session_kind=data.get("session_kind"),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )


@dataclass(frozen=True)
class DiscoveredSession:
    provider: str
    native_session_id: str | None
    tab_id: int | None
    url: str | None
    session_kind: str | None
    ready: bool
    bindable: bool
    source: str = "open_tab"
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "native_session_id": self.native_session_id,
            "tab_id": self.tab_id,
            "url": self.url,
            "session_kind": self.session_kind,
            "ready": self.ready,
            "bindable": self.bindable,
            "source": self.source,
            "error": self.error,
        }


@dataclass(frozen=True)
class EndpointLocation:
    tab_id: int
    url: str | None
    ready: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"tab_id": self.tab_id, "url": self.url, "ready": self.ready}


@dataclass(frozen=True)
class EndpointResolution:
    endpoint_id: str
    native_session_id: str
    state: EndpointState
    reason_code: str | None
    location: EndpointLocation | None
    candidates: tuple[EndpointLocation, ...]
    observed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint_id": self.endpoint_id,
            "native_session_id": self.native_session_id,
            "state": self.state.value,
            "reason_code": self.reason_code,
            "location": self.location.to_dict() if self.location else None,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True)
class BindResult:
    endpoint: EndpointBinding
    created: bool

    def to_dict(self) -> dict[str, Any]:
        return {"endpoint": self.endpoint.to_dict(), "created": self.created}


@dataclass(frozen=True)
class NormalizedMessage:
    message_id: str | None
    message_ids: tuple[str, ...]
    role: str | None
    content: str
    turn_group_id: str | None
    turn_position: int | None
    status: str | None
    evidence: dict[str, Any]

    @classmethod
    def from_adapter(cls, message: dict[str, Any]) -> "NormalizedMessage":
        message_ids = tuple(message.get("message_ids") or ())
        message_id = message.get("message_id") or (message_ids[0] if message_ids else None)
        return cls(
            message_id=message_id,
            message_ids=message_ids,
            role=message.get("role"),
            content=message.get("content") or "",
            turn_group_id=message.get("turn_group_id"),
            turn_position=message.get("turn_position"),
            status=message.get("status"),
            evidence={
                "turn_key": message.get("turn_key"),
                "message_ids": list(message_ids),
                "dom_order": message.get("order"),
                "adapter_status": message.get("status"),
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "message_ids": list(self.message_ids),
            "role": self.role,
            "content": self.content,
            "turn_group_id": self.turn_group_id,
            "turn_position": self.turn_position,
            "status": self.status,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class EndpointSnapshot:
    endpoint_id: str
    native_session_id: str
    state: EndpointState
    captured_at: str
    session_kind: str | None
    url: str | None
    generation: dict[str, Any]
    messages: list[NormalizedMessage]
    history_scope: str = field(default="rendered_dom", init=False)
    absence_means_deleted: bool = field(default=False, init=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint_id": self.endpoint_id,
            "native_session_id": self.native_session_id,
            "state": self.state.value,
            "captured_at": self.captured_at,
            "session_kind": self.session_kind,
            "url": self.url,
            "generation": dict(self.generation),
            "messages": [message.to_dict() for message in self.messages],
            "history_scope": self.history_scope,
            "absence_means_deleted": self.absence_means_deleted,
        }
