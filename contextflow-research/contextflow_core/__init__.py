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
    "WebSocketChatGPTAdapter",
    "default_registry_path",
]
