"""DSH runtime adapter (service kind, local HTTP RPC).

DSH is a long-lived local Web host, not a CLI. The launcher writes a capability
URL to ``%LOCALAPPDATA%/DSH-Launcher/state.json``; exchanging it for a cookie
grants access to the loopback JSON-RPC surface. Verified against DSH
0.2.1-alpha.1 (C-02 evidence).

Native mapping:

* ``session/create``        -> new session (cwd = workspace)
* ``session/prompt``        -> accepted + requestId (NOT completion)
* ``session/projections``   -> running flag, tokenUsage, inbox, questions
* ``session/page``          -> user/assistant messages, tool calls, turn/end
* ``session/cancel``        -> native cancel; turn/end reason = aborted/user

DSH is the only runtime in this layer with a native cancel. Even so, cancelling
does not roll back file changes already made by the turn.
"""

from __future__ import annotations

import asyncio
import http.cookiejar
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..base import AgentRuntimeAdapter, ConcurrencyGuard
from ..contracts import (
    AgentCapabilities,
    CancelDisposition,
    CancelResult,
    ControlKind,
    EvidenceStatus,
    ExecutionHandle,
    ExecutionRequest,
    ExecutionResult,
    ExecutionSnapshot,
    ExecutionState,
    FailureKind,
    SessionPolicy,
    is_terminal,
    utc_now,
)
from ..errors import (
    AdapterError,
    ExecutionNotFound,
    IdempotencyConflict,
    InvalidRequest,
    RuntimeUnavailable,
    SessionUnavailable,
)
from ..journal import ExecutionJournal, JournalRecord


class _LocalRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        before = urllib.parse.urlsplit(req.full_url)
        after = urllib.parse.urlsplit(newurl)
        if (before.scheme, before.netloc) != (after.scheme, after.netloc):
            raise RuntimeUnavailable("refusing non-loopback DSH auth redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class DshClient:
    """Minimal loopback JSON-RPC client. Never logs the capability URL."""

    def __init__(self, state_path: str | Path | None = None) -> None:
        path = (
            Path(state_path)
            if state_path
            else Path.home() / "AppData/Local/DSH-Launcher/state.json"
        )
        try:
            state = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise RuntimeUnavailable(f"cannot read DSH launcher state: {exc}") from exc
        auth_url = state["url"]
        url = urllib.parse.urlsplit(auth_url)
        if url.scheme not in ("http", "https") or url.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise RuntimeUnavailable("DSH launcher URL is not a loopback origin")
        self.base = urllib.parse.urlunsplit((url.scheme, url.netloc, "", "", ""))
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _LocalRedirect(),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )
        try:
            with self.opener.open(auth_url, timeout=30) as response:
                if response.status != 200:
                    raise RuntimeUnavailable("DSH index authentication failed")
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeUnavailable(f"cannot authenticate to DSH: {exc}") from exc

    def call(
        self,
        endpoint: str,
        request: dict[str, Any] | None = None,
        args: dict[str, Any] | None = None,
    ) -> Any:
        message = {
            "type": "client-request",
            "rpcId": str(uuid.uuid4()),
            "method": endpoint,
            "payload": {"args": args if args is not None else {"request": request or {}}},
        }
        req = urllib.request.Request(
            self.base + "/api/" + endpoint,
            data=json.dumps(message, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Origin": self.base},
            method="POST",
        )
        try:
            with self.opener.open(req, timeout=60) as response:
                envelope = json.load(response)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise RuntimeUnavailable(f"DSH RPC {endpoint} failed: {exc}") from exc
        if envelope.get("rpcId") != message["rpcId"]:
            raise AdapterError("DSH RPC correlation mismatch")
        result = envelope["result"]
        if not result.get("ok"):
            error = result.get("error", {})
            code = str(error.get("code", "DSH_ERROR"))
            message_text = str(error.get("message", "DSH RPC failed"))
            if "session" in code.lower() and "not" in code.lower():
                raise SessionUnavailable(message_text)
            raise AdapterError(f"{code}: {message_text}")
        return result.get("value")

    def sessions(self) -> list[dict[str, Any]]:
        return self.call("session/list", args={"_request": {}})["items"]

    def state(self, session_id: str) -> dict[str, Any]:
        projection = self.call("session/projections", {"sessionId": session_id})
        if projection is None:
            raise SessionUnavailable(f"unknown DSH session {session_id!r}")
        row = next((item for item in self.sessions() if item["sessionId"] == session_id), {})
        values = projection["values"]
        return {
            "sessionId": session_id,
            "running": row.get("running"),
            "cwd": row.get("cwd"),
            "asOfSeq": projection["asOfSeq"],
            "title": values.get("title"),
            "sessionStats": values.get("sessionStats"),
            "tokenUsage": values.get("tokenUsage"),
            "inbox": values.get("inbox"),
        }

    def history(self, session_id: str, max_messages: int = 20) -> dict[str, Any]:
        projection = self.call("session/projections", {"sessionId": session_id})
        if projection is None:
            raise SessionUnavailable(f"unknown DSH session {session_id!r}")
        return self.call(
            "session/page",
            {
                "address": {"kind": "session", "sessionId": session_id},
                "throughSeq": projection["asOfSeq"],
                "maxMessages": max_messages,
            },
        )


def _last_turn(page: dict[str, Any], turn_number: int | None = None) -> tuple[dict[str, Any] | None, str | None]:
    events = [record["event"] for record in page.get("records", []) if record.get("type") == "event"]
    turn_ends = [event for event in events if event.get("type") == "turn/end"]
    if turn_number is not None:
        matching = [
            event
            for event in turn_ends
            if (event.get("data") or {}).get("turn") == turn_number
        ]
        turn_end = matching[-1] if matching else None
    else:
        turn_end = turn_ends[-1] if turn_ends else None
    final_text = None
    for event in reversed(events):
        if event.get("type") == "assistant/message":
            data = event.get("data", {})
            message = data.get("message", data)
            content = message.get("content", [])
            text = "\n".join(part.get("text", "") for part in content if part.get("type") == "text")
            if text:
                final_text = text
                break
    return turn_end, final_text


@dataclass
class DshExecution:
    execution_id: str
    session_id: str
    request: ExecutionRequest
    request_id: str
    turn_number: int
    started_wall: float
    handle: ExecutionHandle
    cancelled: bool = False
    result: ExecutionResult | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class DshRuntimeAdapter(AgentRuntimeAdapter):
    runtime = "dsh"
    capabilities = AgentCapabilities(
        runtime="dsh",
        control_kind=ControlKind.SERVICE,
        supports_new_session=True,
        supports_session_reuse=True,
        supports_resume=True,
        supports_streaming=False,
        supports_structured_result=True,
        supports_structured_failure=True,
        supports_native_cancel=True,
        supports_usage=True,
        supports_permission_profiles=False,
        supports_workspace_isolation=True,
        supported_permission_profiles=(),
        max_concurrency=None,
        evidence_status=EvidenceStatus.VERIFIED,
        notes=(
            "local HTTP RPC on loopback; launcher capability URL exchanged for cookie",
            "session/prompt returns accepted+requestId, not completion",
            "completion observed via session/projections and turn/end",
            "native cancel: turn/end reason aborted/user",
            "cancelled turns may already have produced file side effects",
            "turn/end completed does not mean the business goal was met",
            "no streaming subscription used; polling only",
        ),
    )

    def __init__(
        self,
        journal_dir: str | Path,
        launcher_state: str | None = None,
        poll_interval: float = 2.0,
        max_concurrency: int | None = None,
    ) -> None:
        self.journal = ExecutionJournal(journal_dir)
        self.launcher_state = launcher_state
        self.poll_interval = poll_interval
        self._guard = ConcurrencyGuard(max_concurrency)
        self._client: DshClient | None = None
        self._live: dict[str, DshExecution] = {}
        self._results: dict[str, ExecutionResult] = {}
        self._lock = threading.RLock()

    @property
    def client(self) -> DshClient:
        if self._client is None:
            self._client = DshClient(self.launcher_state)
        return self._client

    # -- interface --------------------------------------------------------
    async def probe(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._probe_sync)

    def _probe_sync(self) -> dict[str, Any]:
        sessions = self.client.sessions()
        return {
            "runtime": self.runtime,
            "origin": self.client.base,
            "authenticated": True,
            "sessionCount": len(sessions),
            "capabilities": self.capabilities.to_dict(),
        }

    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        self._validate_request(request)
        return await asyncio.to_thread(self._start_sync, request)

    def _validate_request(self, request: ExecutionRequest) -> None:
        if request.runtime != self.runtime:
            raise InvalidRequest(
                f"request runtime {request.runtime!r} does not match adapter {self.runtime!r}"
            )
        if request.session_policy is SessionPolicy.NEW and request.native_session_id is not None:
            raise InvalidRequest("new policy cannot carry a native session id")

    def _start_sync(self, request: ExecutionRequest) -> ExecutionHandle:
        existing = self.journal.lookup(request.caller_ref, request.fingerprint())
        if existing is not None:
            if existing.state is ExecutionState.UNKNOWN:
                raise IdempotencyConflict(
                    "an earlier execution for this caller_ref has unknown outcome; "
                    "reconcile it instead of re-executing",
                    details={"caller_ref": request.caller_ref, "execution_id": existing.execution_id},
                )
            return self._handle_from_record(existing)
        self._guard.acquire()
        try:
            session_id = self._resolve_session(request)
            state = self.client.state(session_id)
            turns = int((state.get("sessionStats") or {}).get("turns") or 0)
            request_id = str(uuid.uuid4())
            self.client.call(
                "session/prompt",
                {
                    "sessionId": session_id,
                    "requestId": request_id,
                    "mode": "queue",
                    "content": [{"type": "text", "text": request.instruction}],
                    "clientTimeZone": "Asia/Shanghai",
                },
            )
        except Exception:
            self._guard.release()
            raise
        execution_id = "cfexec_" + uuid.uuid4().hex
        handle = ExecutionHandle(
            execution_id=execution_id,
            runtime=self.runtime,
            caller_ref=request.caller_ref,
            state=ExecutionState.RUNNING,
            created_at=utc_now(),
            native_session_id=session_id,
            session_policy=request.session_policy,
            resumable=True,
            pid=None,
            diagnostics={"requestId": request_id, "turn": turns + 1},
        )
        live = DshExecution(
            execution_id=execution_id,
            session_id=session_id,
            request=request,
            request_id=request_id,
            turn_number=turns + 1,
            started_wall=time.time(),
            handle=handle,
        )
        with self._lock:
            self._live[execution_id] = live
        self.journal.register(
            JournalRecord(
                execution_id=execution_id,
                runtime=self.runtime,
                caller_ref=request.caller_ref,
                fingerprint=request.fingerprint(),
                state=ExecutionState.RUNNING,
                created_at=handle.created_at,
                updated_at=handle.created_at,
                native_session_id=session_id,
                pid=None,
                resumable=True,
            )
        )
        return handle

    def _resolve_session(self, request: ExecutionRequest) -> str:
        if request.session_policy is SessionPolicy.REUSE:
            session_id = request.native_session_id
            if not session_id:
                raise InvalidRequest("reuse policy requires a native session id")
            try:
                self.client.state(session_id)
            except SessionUnavailable:
                raise
            return session_id
        if request.session_policy is SessionPolicy.AUTO and request.native_session_id:
            self.client.state(request.native_session_id)
            return request.native_session_id
        created = self.client.call("session/create", {"cwd": str(Path(request.workdir).resolve())})
        session_id = created.get("sessionId")
        if not session_id:
            raise AdapterError("DSH session/create returned no sessionId")
        return str(session_id)

    def _handle_from_record(self, record: JournalRecord) -> ExecutionHandle:
        return ExecutionHandle(
            execution_id=record.execution_id,
            runtime=record.runtime,
            caller_ref=record.caller_ref,
            state=record.state,
            created_at=record.created_at,
            native_session_id=record.native_session_id,
            session_policy=SessionPolicy.AUTO,
            resumable=record.resumable,
            pid=None,
            diagnostics={"duplicate_suppressed": True, "journal_state": record.state.value},
        )

    async def inspect(self, execution_id: str) -> ExecutionSnapshot:
        return await asyncio.to_thread(self._inspect_sync, execution_id)

    def _inspect_sync(self, execution_id: str) -> ExecutionSnapshot:
        with self._lock:
            live = self._live.get(execution_id)
        if live is not None:
            self._refresh(live)
            if live.result is not None:
                return live.result.snapshot
            return self._snapshot(live, ExecutionState.RUNNING)
        record = self.journal.get(execution_id)
        if record is None:
            raise ExecutionNotFound(
                f"unknown execution id {execution_id!r}",
                details={"execution_id": execution_id},
            )
        return self._snapshot_from_record(record)

    def _refresh(self, live: DshExecution) -> None:
        if live.result is not None:
            return
        state = self.client.state(live.session_id)
        if state.get("running"):
            return
        page = self.client.history(live.session_id, max_messages=30)
        turn_end, final_text = _last_turn(page, live.turn_number)
        if turn_end is not None:
            result = self._finalize(live, turn_end, final_text, state)
            self._store_result(live, result)
            return
        inbox = state.get("inbox") or {}
        pending = any(inbox.get(key) for key in ("next-turn", "next-step"))
        if pending:
            return
        if live.cancelled:
            result = self._finalize_cancelled(live, state)
            self._store_result(live, result)

    def _finalize(
        self,
        live: DshExecution,
        turn_end: dict[str, Any],
        final_text: str | None,
        state: dict[str, Any],
    ) -> ExecutionResult:
        reason = ((turn_end.get("data") or {}).get("reason") or {})
        kind = reason.get("kind")
        usage = {
            "tokenUsage": state.get("tokenUsage"),
            "sessionStats": state.get("sessionStats"),
        }
        if kind == "completed":
            snapshot = self._snapshot(
                live,
                ExecutionState.FINISHED,
                native_state="completed",
                output_text=final_text,
                usage=usage,
                result_complete=True,
                turn_end=turn_end,
            )
            return ExecutionResult(
                snapshot=snapshot, failure_kind=FailureKind.NONE, final_text=final_text
            )
        if kind == "aborted":
            snapshot = self._snapshot(
                live,
                ExecutionState.CANCELLED,
                native_state="aborted",
                output_text=final_text,
                usage=usage,
                result_complete=True,
                turn_end=turn_end,
                error={"code": "EXECUTION_ABORTED", "reason": reason},
            )
            return ExecutionResult(
                snapshot=snapshot, failure_kind=FailureKind.CANCELLED, final_text=final_text
            )
        snapshot = self._snapshot(
            live,
            ExecutionState.FAILED,
            native_state=str(kind or "unknown"),
            output_text=final_text,
            usage=usage,
            result_complete=True,
            turn_end=turn_end,
            error={"code": "TURN_END_NOT_COMPLETED", "reason": reason},
        )
        return ExecutionResult(
            snapshot=snapshot, failure_kind=FailureKind.RUNTIME_ERROR, final_text=final_text
        )

    def _finalize_cancelled(self, live: DshExecution, state: dict[str, Any]) -> ExecutionResult:
        snapshot = self._snapshot(
            live,
            ExecutionState.UNKNOWN,
            native_state="cancel_unconfirmed",
            usage={
                "tokenUsage": state.get("tokenUsage"),
                "sessionStats": state.get("sessionStats"),
            },
            result_complete=False,
            error={
                "code": "CANCEL_UNCONFIRMED",
                "message": "cancel requested but no turn/end was observed for this turn",
            },
        )
        return ExecutionResult(
            snapshot=snapshot, failure_kind=FailureKind.UNKNOWN, final_text=None
        )

    async def cancel(self, execution_id: str) -> CancelResult:
        return await asyncio.to_thread(self._cancel_sync, execution_id)

    def _cancel_sync(self, execution_id: str) -> CancelResult:
        with self._lock:
            live = self._live.get(execution_id)
        if live is None:
            record = self.journal.get(execution_id)
            if record is None:
                raise ExecutionNotFound(
                    f"unknown execution id {execution_id!r}",
                    details={"execution_id": execution_id},
                )
            return CancelResult(
                execution_id=execution_id,
                disposition=CancelDisposition.UNSUPPORTED,
                requested_at=utc_now(),
                snapshot=self._snapshot_from_record(record),
                note="execution is not live in this adapter process; nothing to cancel",
            )
        if live.result is not None:
            return CancelResult(
                execution_id=execution_id,
                disposition=CancelDisposition.UNSUPPORTED,
                requested_at=utc_now(),
                snapshot=live.result.snapshot,
                note="execution already reached a terminal state; cancel is a no-op",
            )
        live.cancelled = True
        self.client.call("session/cancel", {"sessionId": live.session_id})
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            state = self.client.state(live.session_id)
            if not state.get("running"):
                break
            time.sleep(self.poll_interval)
        self._refresh(live)
        if live.result is not None:
            snapshot = live.result.snapshot
            disposition = (
                CancelDisposition.REMOTE_CONFIRMED_STOPPED
                if snapshot.state is ExecutionState.CANCELLED
                else CancelDisposition.UNKNOWN
            )
            return CancelResult(
                execution_id=execution_id,
                disposition=disposition,
                requested_at=utc_now(),
                confirmed_at=utc_now() if disposition is CancelDisposition.REMOTE_CONFIRMED_STOPPED else None,
                snapshot=snapshot,
                note=(
                    "DSH confirmed the turn aborted; file side effects are not rolled back"
                    if disposition is CancelDisposition.REMOTE_CONFIRMED_STOPPED
                    else "cancel requested but the terminal state could not be confirmed"
                ),
            )
        result = self._finalize_cancelled(live, self.client.state(live.session_id))
        self._store_result(live, result)
        return CancelResult(
            execution_id=execution_id,
            disposition=CancelDisposition.UNKNOWN,
            requested_at=utc_now(),
            snapshot=result.snapshot,
            note="cancel requested but no terminal event was observed",
        )

    async def result(self, execution_id: str) -> ExecutionResult:
        return await asyncio.to_thread(self._result_sync, execution_id)

    async def wait(self, execution_id: str, timeout_s: float | None = None) -> ExecutionResult:
        with self._lock:
            live = self._live.get(execution_id)
        if timeout_s is None and live is not None:
            deadline = live.started_wall + float(live.request.timeout_s)
        elif timeout_s is not None:
            deadline = time.time() + timeout_s
        else:
            deadline = None
        while True:
            snapshot = await self.inspect(execution_id)
            if is_terminal(snapshot.state):
                return await self.result(execution_id)
            if deadline is not None and time.time() >= deadline:
                cancel_result = await self.cancel(execution_id)
                return ExecutionResult(
                    snapshot=cancel_result.snapshot,
                    failure_kind=FailureKind.TIMEOUT,
                    final_text=cancel_result.snapshot.output_text,
                    cancel=cancel_result,
                )
            await asyncio.sleep(self.poll_interval)

    def _result_sync(self, execution_id: str) -> ExecutionResult:
        with self._lock:
            cached = self._results.get(execution_id)
            live = self._live.get(execution_id)
        if cached is not None:
            return cached
        if live is not None:
            self._refresh(live)
            if live.result is not None:
                return live.result
            snapshot = self._snapshot(live, ExecutionState.RUNNING)
            return ExecutionResult(snapshot=snapshot, failure_kind=FailureKind.NONE)
        record = self.journal.get(execution_id)
        if record is None:
            raise ExecutionNotFound(
                f"unknown execution id {execution_id!r}",
                details={"execution_id": execution_id},
            )
        snapshot = self._snapshot_from_record(record)
        failure = FailureKind.UNKNOWN if snapshot.state is ExecutionState.UNKNOWN else FailureKind.NONE
        return ExecutionResult(snapshot=snapshot, failure_kind=failure)

    async def close(self) -> None:
        with self._lock:
            live_ids = list(self._live)
        for execution_id in live_ids:
            try:
                await self.cancel(execution_id)
            except Exception:
                pass

    # -- helpers ----------------------------------------------------------
    def _store_result(self, live: DshExecution, result: ExecutionResult) -> None:
        with self._lock:
            live.result = result
            self._results[live.execution_id] = result
        try:
            self.journal.update(
                live.execution_id,
                state=result.state,
                native_session_id=live.session_id,
                note=result.failure_kind.value,
            )
        except KeyError:
            pass
        self._guard.release()

    def _snapshot(
        self,
        live: DshExecution,
        state: ExecutionState,
        *,
        native_state: str | None = None,
        output_text: str | None = None,
        usage: dict[str, Any] | None = None,
        result_complete: bool = False,
        error: dict[str, Any] | None = None,
        turn_end: dict[str, Any] | None = None,
    ) -> ExecutionSnapshot:
        return ExecutionSnapshot(
            execution_id=live.execution_id,
            runtime=self.runtime,
            caller_ref=live.request.caller_ref,
            state=state,
            native_state=native_state,
            native_session_id=live.session_id,
            exit_code=None,
            output_text=output_text,
            error=error,
            started_at=_iso(live.started_wall),
            ended_at=utc_now() if is_terminal(state) else None,
            usage=usage or {},
            result_complete=result_complete,
            side_effects_possible=True,
            retry_safe=False,
            diagnostics={
                "requestId": live.request_id,
                "turn": live.turn_number,
                "turn_end": turn_end,
            },
        )

    def _snapshot_from_record(self, record: JournalRecord) -> ExecutionSnapshot:
        return ExecutionSnapshot(
            execution_id=record.execution_id,
            runtime=record.runtime,
            caller_ref=record.caller_ref,
            state=record.state,
            native_session_id=record.native_session_id,
            started_at=record.created_at,
            ended_at=record.updated_at if is_terminal(record.state) else None,
            result_complete=False,
            side_effects_possible=True,
            retry_safe=False,
            diagnostics={"journal_only": True, "note": record.note},
        )


def _iso(epoch: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
