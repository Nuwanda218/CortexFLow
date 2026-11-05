"""ChatGPT Endpoint discovery, binding, resolution, and reading."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .adapter import ChatGPTAdapter
from .errors import ContextFlowError
from .models import (
    BindResult,
    DiscoveredSession,
    EndpointLocation,
    EndpointResolution,
    EndpointSnapshot,
    EndpointState,
    NormalizedMessage,
)
from .registry import EndpointRegistry


CHATGPT_PROVIDER = "chatgpt_web"
RETRYABLE_READ_CODES = {
    "BRIDGE_DISCONNECTED",
    "BRIDGE_TIMEOUT",
    "BRIDGE_UNAVAILABLE",
    "CONTENT_SCRIPT_UNAVAILABLE",
    "READ_TIMEOUT",
    "SESSION_CHANGED",
    "SESSION_NOT_FOUND",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class EndpointService:
    def __init__(
        self,
        adapter: ChatGPTAdapter,
        registry: EndpointRegistry,
        provider: str = CHATGPT_PROVIDER,
    ) -> None:
        self.adapter = adapter
        self.registry = registry
        self.provider = provider
        self._last_locations: dict[str, EndpointLocation] = {}

    async def discover(self) -> list[DiscoveredSession]:
        sessions = await self.adapter.list_sessions()
        return [self._normalize_discovery(item) for item in sessions]

    async def bind(self, session_id: str) -> BindResult:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ContextFlowError(
                "INVALID_ARGUMENT",
                "session_id must be a non-empty string",
            )
        discovered = await self.discover()
        matches = [
            item for item in discovered
            if item.native_session_id == session_id
        ]
        if not matches:
            raise ContextFlowError(
                "SESSION_NOT_FOUND",
                f"no open ChatGPT tab shows session {session_id}",
                retryable=True,
            )
        if len(matches) > 1:
            raise ContextFlowError(
                "AMBIGUOUS_SESSION",
                f"session {session_id} is open in multiple tabs",
                details={"tab_ids": [item.tab_id for item in matches]},
            )
        target = matches[0]
        if not target.ready or target.tab_id is None:
            error = target.error or {}
            raise ContextFlowError(
                error.get("code") or "SESSION_UNAVAILABLE",
                error.get("message") or f"session {session_id} is not ready",
                retryable=True,
                details={"tab_id": target.tab_id},
            )

        binding, created = self.registry.register(
            self.provider,
            session_id,
            target.session_kind,
        )
        self._last_locations[binding.endpoint_id] = EndpointLocation(
            tab_id=target.tab_id,
            url=target.url,
            ready=True,
        )
        return BindResult(endpoint=binding, created=created)

    async def resolve(self, endpoint_id: str) -> EndpointResolution:
        binding = self.registry.get(endpoint_id)
        if binding is None:
            raise ContextFlowError(
                "ENDPOINT_NOT_FOUND",
                f"unknown endpoint {endpoint_id}",
            )

        try:
            discovered = await self.discover()
        except ContextFlowError as exc:
            return self._offline_resolution(binding, exc.code)

        matches = [
            item for item in discovered
            if item.native_session_id == binding.native_session_id
        ]
        if len(matches) > 1:
            return EndpointResolution(
                endpoint_id=binding.endpoint_id,
                native_session_id=binding.native_session_id,
                state=EndpointState.AMBIGUOUS,
                reason_code="AMBIGUOUS_SESSION",
                location=None,
                candidates=tuple(
                    EndpointLocation(item.tab_id, item.url, item.ready)
                    for item in matches
                    if item.tab_id is not None
                ),
                observed_at=_now_iso(),
            )

        if len(matches) == 1:
            target = matches[0]
            if target.ready and target.tab_id is not None:
                location = EndpointLocation(target.tab_id, target.url, True)
                self._last_locations[binding.endpoint_id] = location
                return EndpointResolution(
                    endpoint_id=binding.endpoint_id,
                    native_session_id=binding.native_session_id,
                    state=EndpointState.AVAILABLE,
                    reason_code=None,
                    location=location,
                    candidates=(location,),
                    observed_at=_now_iso(),
                )
            error = target.error or {}
            return self._offline_resolution(
                binding,
                error.get("code") or "SESSION_UNAVAILABLE",
                EndpointLocation(target.tab_id, target.url, False)
                if target.tab_id is not None else None,
            )

        last_location = self._last_locations.get(binding.endpoint_id)
        if last_location is not None:
            same_tab = next(
                (item for item in discovered if item.tab_id == last_location.tab_id),
                None,
            )
            if same_tab is not None and not same_tab.ready:
                error = same_tab.error or {}
                return self._offline_resolution(
                    binding,
                    error.get("code") or "SESSION_UNAVAILABLE",
                    EndpointLocation(last_location.tab_id, same_tab.url, False),
                )
        return self._offline_resolution(binding, "SESSION_NOT_FOUND")

    async def read(self, endpoint_id: str) -> EndpointSnapshot:
        binding = self.registry.get(endpoint_id)
        if binding is None:
            raise ContextFlowError(
                "ENDPOINT_NOT_FOUND",
                f"unknown endpoint {endpoint_id}",
            )

        for attempt in range(2):
            resolution = await self.resolve(endpoint_id)
            if resolution.state is not EndpointState.AVAILABLE or resolution.location is None:
                raise self._resolution_error(resolution)
            try:
                raw = await self.adapter.get_snapshot(
                    binding.native_session_id,
                    tab_id=resolution.location.tab_id,
                )
                return self._normalize_snapshot(binding, raw)
            except ContextFlowError as exc:
                if attempt == 0 and exc.code in RETRYABLE_READ_CODES:
                    continue
                raise
        raise AssertionError("unreachable")

    def _normalize_snapshot(self, binding, raw: dict[str, Any]) -> EndpointSnapshot:
        snapshot = raw.get("snapshot") if isinstance(raw, dict) else None
        if not isinstance(snapshot, dict):
            raise ContextFlowError(
                "ADAPTER_PROTOCOL_ERROR",
                "get_snapshot response does not contain a snapshot object",
            )
        session = snapshot.get("session")
        if not isinstance(session, dict):
            raise ContextFlowError(
                "ADAPTER_PROTOCOL_ERROR",
                "snapshot does not contain session metadata",
            )
        observed_session_id = session.get("session_id")
        if observed_session_id != binding.native_session_id:
            raise ContextFlowError(
                "SESSION_CHANGED",
                f"snapshot returned session {observed_session_id}, expected {binding.native_session_id}",
                retryable=True,
                details={"observed_session_id": observed_session_id},
            )
        raw_messages = snapshot.get("messages")
        if not isinstance(raw_messages, list):
            raise ContextFlowError(
                "ADAPTER_PROTOCOL_ERROR",
                "snapshot messages must be a list",
            )
        page = snapshot.get("page") if isinstance(snapshot.get("page"), dict) else {}
        generation = (
            snapshot.get("generation")
            if isinstance(snapshot.get("generation"), dict)
            else {}
        )
        return EndpointSnapshot(
            endpoint_id=binding.endpoint_id,
            native_session_id=binding.native_session_id,
            state=EndpointState.AVAILABLE,
            captured_at=(
                raw.get("captured_at")
                or snapshot.get("at")
                or _now_iso()
            ),
            session_kind=session.get("kind") or binding.session_kind,
            url=page.get("url") if isinstance(page.get("url"), str) else None,
            generation=generation,
            messages=[
                NormalizedMessage.from_adapter(message)
                for message in raw_messages
                if isinstance(message, dict)
            ],
        )

    def _resolution_error(self, resolution: EndpointResolution) -> ContextFlowError:
        code = resolution.reason_code or "ENDPOINT_UNAVAILABLE"
        return ContextFlowError(
            code,
            f"endpoint {resolution.endpoint_id} is {resolution.state.value}",
            retryable=resolution.state is EndpointState.OFFLINE,
            details={"resolution": resolution.to_dict()},
        )

    def _offline_resolution(
        self,
        binding,
        reason_code: str,
        location: EndpointLocation | None = None,
    ) -> EndpointResolution:
        if location is None:
            last = self._last_locations.get(binding.endpoint_id)
            if last is not None:
                location = EndpointLocation(last.tab_id, last.url, False)
        return EndpointResolution(
            endpoint_id=binding.endpoint_id,
            native_session_id=binding.native_session_id,
            state=EndpointState.OFFLINE,
            reason_code=reason_code,
            location=location,
            candidates=(),
            observed_at=_now_iso(),
        )

    def _normalize_discovery(self, item: dict[str, Any]) -> DiscoveredSession:
        session_id = item.get("session_id")
        ready = item.get("ready") is True
        tab_id = item.get("tab_id")
        return DiscoveredSession(
            provider=self.provider,
            native_session_id=session_id if isinstance(session_id, str) and session_id else None,
            tab_id=tab_id if isinstance(tab_id, int) else None,
            url=item.get("url") if isinstance(item.get("url"), str) else None,
            session_kind=item.get("kind") if isinstance(item.get("kind"), str) else None,
            ready=ready,
            bindable=ready and isinstance(session_id, str) and bool(session_id) and isinstance(tab_id, int),
            source="open_tab",
            error=item.get("error") if isinstance(item.get("error"), dict) else None,
        )
