"""ContextFlow Core public API."""

from .adapter import ChatGPTAdapter, WebSocketChatGPTAdapter
from .errors import ContextFlowError
from .models import (
    BindResult,
    DiscoveredSession,
    EndpointBinding,
    EndpointLocation,
    EndpointResolution,
    EndpointSnapshot,
    EndpointState,
    NormalizedMessage,
)
from .registry import EndpointRegistry, default_registry_path
from .service import EndpointService
from .submission import (
    MockCoreReceiver,
    accepted_ack,
    canonical_envelope,
    envelope_fingerprint,
    rejected_ack,
    validate_envelope,
)

__all__ = [
    "BindResult",
    "ChatGPTAdapter",
    "ContextFlowError",
    "DiscoveredSession",
    "EndpointBinding",
    "EndpointLocation",
    "EndpointResolution",
    "EndpointRegistry",
    "EndpointSnapshot",
    "EndpointState",
    "EndpointService",
    "NormalizedMessage",
    "MockCoreReceiver",
    "WebSocketChatGPTAdapter",
    "accepted_ack",
    "canonical_envelope",
    "default_registry_path",
    "envelope_fingerprint",
    "rejected_ack",
    "validate_envelope",
]
