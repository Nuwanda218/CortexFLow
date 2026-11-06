"""Read persistent Endpoint identities without probing browser availability."""

from __future__ import annotations

import asyncio
from pathlib import Path

from .contracts import EndpointId, EndpointIdentity, NativeSessionId
from .errors import ContextFlowError
from .registry import EndpointRegistry, default_registry_path


class RegistryEndpointIdentityReader:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_registry_path()

    async def get_identity(self, endpoint_id: EndpointId) -> EndpointIdentity | None:
        if not isinstance(endpoint_id, EndpointId):
            raise ContextFlowError('INVALID_ARGUMENT', 'endpoint_id must be an EndpointId')
        return await asyncio.to_thread(self._get_identity, endpoint_id)

    def _get_identity(self, endpoint_id: EndpointId) -> EndpointIdentity | None:
        # Bindings may have been added by another process since the last read.
        binding = EndpointRegistry(self.path).get(str(endpoint_id))
        if binding is None:
            return None
        return EndpointIdentity(
            endpoint_id=EndpointId(binding.endpoint_id),
            provider=binding.provider,
            native_session_id=NativeSessionId(binding.native_session_id),
        )
