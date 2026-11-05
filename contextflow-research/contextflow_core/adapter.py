"""Adapter boundary between ContextFlow Core and the existing WebSocket bridge."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Protocol

from websockets.asyncio.client import connect

from .errors import ContextFlowError


DEFAULT_BRIDGE_URI = "ws://127.0.0.1:8766/ws?token=dev-token-cf-exp-002"
DEFAULT_BRIDGE_ORIGIN = "http://127.0.0.1:8766"


class ChatGPTAdapter(Protocol):
    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def list_sessions(self) -> list[dict[str, Any]]: ...

    async def get_snapshot(
        self,
        session_id: str,
        tab_id: int | None = None,
    ) -> dict[str, Any]: ...


class WebSocketChatGPTAdapter:
    def __init__(
        self,
        uri: str = DEFAULT_BRIDGE_URI,
        origin: str = DEFAULT_BRIDGE_ORIGIN,
        request_timeout: float = 20.0,
    ) -> None:
        self.uri = uri
        self.origin = origin
        self.request_timeout = request_timeout
        self._ws = None
        self._reader: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._connect_lock = asyncio.Lock()

    async def connect(self) -> None:
        async with self._connect_lock:
            if self._ws is not None:
                return
            try:
                ws = await connect(self.uri, origin=self.origin)
            except Exception as exc:
                raise ContextFlowError(
                    "BRIDGE_UNAVAILABLE",
                    f"cannot connect to {self.uri}: {exc}",
                    retryable=True,
                ) from exc
            self._ws = ws
            self._reader = asyncio.create_task(self._read_loop(ws))

    async def close(self) -> None:
        async with self._connect_lock:
            ws = self._ws
            reader = self._reader
            self._ws = None
            self._reader = None
        if ws is not None:
            await ws.close()
        if reader is not None and reader is not asyncio.current_task():
            reader.cancel()
            try:
                await reader
            except (asyncio.CancelledError, Exception):
                pass
        self._fail_pending("BRIDGE_DISCONNECTED", "bridge connection closed")

    async def list_sessions(self) -> list[dict[str, Any]]:
        data = await self._call({"type": "list_sessions"})
        sessions = data.get("sessions", [])
        return sessions if isinstance(sessions, list) else []

    async def get_snapshot(
        self,
        session_id: str,
        tab_id: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "get_snapshot",
            "session_id": session_id,
        }
        if tab_id is not None:
            payload["tab_id"] = tab_id
        return await self._call(payload)

    async def _call(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.request(payload)
        if not response.get("success"):
            error = response.get("error") or {}
            code = error.get("code") or "ADAPTER_ERROR"
            raise ContextFlowError(
                code,
                error.get("message") or "adapter request failed",
                retryable=code in {
                    "BRIDGE_DISCONNECTED",
                    "BRIDGE_UNAVAILABLE",
                    "CONTENT_SCRIPT_UNAVAILABLE",
                    "READ_TIMEOUT",
                },
                details=error.get("details") or {},
            )
        data = response.get("data")
        return data if isinstance(data, dict) else {}

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self.connect()
        request = dict(payload)
        request_id = request.setdefault("id", f"req-{uuid.uuid4().hex}")
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        ws = self._ws
        try:
            if ws is None:
                raise ContextFlowError(
                    "BRIDGE_DISCONNECTED",
                    "bridge connection closed before send",
                    retryable=True,
                )
            await ws.send(json.dumps(request, ensure_ascii=False))
            return await asyncio.wait_for(future, timeout=self.request_timeout)
        except asyncio.TimeoutError as exc:
            raise ContextFlowError(
                "BRIDGE_TIMEOUT",
                f"no bridge result within {self.request_timeout}s",
                retryable=True,
            ) from exc
        except ContextFlowError:
            raise
        except Exception as exc:
            if self._ws is ws:
                self._ws = None
            raise ContextFlowError(
                "BRIDGE_DISCONNECTED",
                f"bridge connection failed: {exc}",
                retryable=True,
            ) from exc
        finally:
            self._pending.pop(request_id, None)

    async def _read_loop(self, ws) -> None:
        try:
            async for raw in ws:
                try:
                    message = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if message.get("type") == "welcome":
                    continue
                request_id = message.get("id")
                future = self._pending.get(request_id)
                if future is not None and not future.done():
                    future.set_result(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            if self._ws is ws:
                self._ws = None
                self._reader = None
            self._fail_pending("BRIDGE_DISCONNECTED", "bridge connection closed")

    def _fail_pending(self, code: str, message: str) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(
                    ContextFlowError(code, message, retryable=True)
                )
