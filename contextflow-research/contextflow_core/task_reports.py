"""C-06: persist an Agent report, return it, and record Supervisor review."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5

from . import contracts as c
from .agent_runtime import utc_now
from .contracts._validation import thaw_json
from .errors import ContextFlowError
from .execution_store import SQLiteExecutionRepository
from .task_store import SQLiteTaskRepository


_SCHEMA = """CREATE TABLE IF NOT EXISTS task_report_returns (
    report_id TEXT PRIMARY KEY NOT NULL,
    message_id TEXT NOT NULL UNIQUE REFERENCES messages(message_id),
    task_delivery_id TEXT NOT NULL UNIQUE REFERENCES task_executions(delivery_id),
    return_delivery_id TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    execution_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    endpoint_id TEXT NOT NULL,
    native_session_id TEXT NOT NULL,
    execution_facts_json TEXT NOT NULL,
    outbound_text TEXT NOT NULL,
    review_code_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'sending', 'confirmed', 'failed', 'unknown')),
    error_json TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    review_decision TEXT CHECK (review_decision IN ('accept', 'request_changes', 'reject')),
    review_note TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)"""
_PREVIEW_LIMIT = 8000


class ReviewDecision(str, Enum):
    ACCEPT = "accept"
    REQUEST_CHANGES = "request_changes"
    REJECT = "reject"


class ReportSender(Protocol):
    async def send_message_to_endpoint(
        self, endpoint_id: c.EndpointId, native_session_id: c.NativeSessionId, text: str,
    ) -> c.DeliveryOutcome: ...


@dataclass(frozen=True)
class TaskReport:
    report_id: c.ReportId
    task_id: c.TaskId
    task_delivery_id: c.DeliveryId
    execution_id: str
    agent_id: c.AgentId
    message_id: c.MessageId
    native_session_id: c.NativeSessionId
    execution_facts: dict
    outbound_text: str = field(repr=False)
    delivery: c.ReportDelivery
    review_decision: ReviewDecision | None
    review_note: str | None
    reviewed_at: str | None

    @property
    def review_state(self) -> str:
        if self.review_decision is not None:
            return self.review_decision.value
        if self.delivery.state is c.DeliveryState.CONFIRMED:
            return "awaiting_review"
        return "awaiting_delivery"


def _report(row) -> TaskReport:
    message_id = c.MessageId(row["message_id"])
    delivery = c.ReportDelivery(
        delivery_id=c.DeliveryId(row["return_delivery_id"]), subject_id=message_id,
        target_id=c.EndpointId(row["endpoint_id"]), state=c.DeliveryState(row["state"]),
        created_at=row["created_at"], updated_at=row["updated_at"],
        error=c.ErrorInfo(**json.loads(row["error_json"])) if row["error_json"] else None,
        evidence=json.loads(row["evidence_json"]),
    )
    return TaskReport(
        report_id=c.ReportId(row["report_id"]), task_id=c.TaskId(row["task_id"]),
        task_delivery_id=c.DeliveryId(row["task_delivery_id"]), execution_id=row["execution_id"],
        agent_id=c.AgentId(row["agent_id"]), message_id=message_id,
        native_session_id=c.NativeSessionId(row["native_session_id"]),
        execution_facts=json.loads(row["execution_facts_json"]), outbound_text=row["outbound_text"],
        delivery=delivery, review_decision=ReviewDecision(row["review_decision"]) if row["review_decision"] else None,
        review_note=row["review_note"], reviewed_at=row["reviewed_at"],
    )


def _error_outcome(state: c.DeliveryState, code: str, message: str) -> c.DeliveryOutcome:
    return c.DeliveryOutcome(state=state, error=c.ErrorInfo(code=code, message=message))


def _review_code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


class TaskReportService:
    def __init__(
        self, tasks: SQLiteTaskRepository, messages: c.MessageStore, identities: c.EndpointIdentityReader,
    ) -> None:
        self.tasks = tasks
        self.messages = messages
        self.identities = identities
        self.executions = SQLiteExecutionRepository(tasks)
        with tasks.transaction() as db:
            db.execute(_SCHEMA)

    @staticmethod
    def _get(db, report_id: c.ReportId) -> TaskReport:
        row = db.execute("SELECT * FROM task_report_returns WHERE report_id=?", (report_id,)).fetchone()
        if row is None:
            raise ContextFlowError("REPORT_NOT_FOUND", "report does not exist")
        return _report(row)

    async def get_report(self, report_id: c.ReportId) -> TaskReport:
        def read():
            with self.tasks.connection() as db:
                return self._get(db, report_id)
        return await asyncio.to_thread(read)

    async def generate_report(self, delivery_id: c.DeliveryId) -> TaskReport:
        def find():
            with self.tasks.connection() as db:
                row = db.execute("SELECT * FROM task_report_returns WHERE task_delivery_id=?", (delivery_id,)).fetchone()
                return _report(row) if row is not None else None
        existing = await asyncio.to_thread(find)
        if existing is not None:
            await self.messages.mark_processed(existing.message_id)
            return existing

        execution = await self.executions.get(delivery_id)
        if execution is None or execution.execution_id is None or not execution.has_final_result:
            raise ContextFlowError("REPORT_EXECUTION_NOT_READY", "a saved, conclusive execution result is required")
        task = await self.tasks.get_task(execution.task_id)
        original = await self.messages.get_message(task.source_message_id)
        if original is None or not isinstance(original.envelope, c.SupervisorEnvelope):
            raise ContextFlowError("REPORT_SOURCE_INVALID", "task has no Supervisor source")
        source = original.envelope.source

        report_id = c.ReportId(f"cfreport_{uuid5(NAMESPACE_URL, f'contextflow:{delivery_id}:{execution.execution_id}').hex}")
        facts = dict(execution.result)
        output = facts.pop("final_text", None)
        facts.update(runtime=execution.runtime, execution_created_at=execution.handle_created_at,
                     result_saved_at=execution.updated_at)
        output_ref = facts.get("output_ref")
        body = output if output and output.strip() else (
            f"原始输出文件：{output_ref}" if output_ref else "Adapter 未记录 Agent 正文。"
        )
        envelope = c.AgentReportEnvelope(
            report_id=report_id, task_id=task.task_id, kind=c.ReportKind.FINAL,
            outcome=c.ReportOutcome.COMPLETED if facts["state"] == "finished" else c.ReportOutcome.FAILED,
            content=c.Content(text=body),
        )
        accepted = await self.messages.accept_message(
            c.AgentSubmission(source_agent_id=task.target_agent_id, envelope=envelope),
        )
        if accepted.status is not c.AcceptStatus.ACCEPTED:
            raise ContextFlowError(accepted.error.code, accepted.error.message)

        now = utc_now()
        excerpt = body
        if output_ref:
            def preview():
                artifact = self.tasks.path.parent / f"{self.tasks.path.name}.execution-results" / f"{delivery_id}.txt"
                try:
                    with artifact.open(encoding="utf-8") as stream:
                        return stream.read(_PREVIEW_LIMIT + 1)
                except (OSError, UnicodeError):
                    return None
            excerpt = await asyncio.to_thread(preview) or body
        if len(excerpt) > _PREVIEW_LIMIT:
            excerpt = excerpt[:_PREVIEW_LIMIT] + "\n\n（仅显示节选，完整正文已保存。）"
        diagnostics = json.dumps(facts, ensure_ascii=False, indent=2)
        review_code = secrets.token_urlsafe(24)
        outbound = (
            f"Agent 执行报告\n\nTaskId: {task.task_id}\nReportId: {report_id}\n"
            f"DeliveryId: {delivery_id}\nExecutionId: {execution.execution_id}\nAgentId: {task.target_agent_id}\n"
            f"审核码: {review_code}\n\n"
            f"执行记录：\n{diagnostics[:_PREVIEW_LIMIT]}\n\nAgent 原始输出：\n{excerpt}"
        )

        def write():
            with self.tasks.transaction() as db:
                db.execute("""
                    INSERT INTO task_report_returns (
                        report_id, message_id, task_delivery_id, return_delivery_id, task_id, execution_id,
                        agent_id, endpoint_id, native_session_id, execution_facts_json, outbound_text,
                        review_code_hash, state, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    ON CONFLICT(report_id) DO NOTHING
                """, (report_id, accepted.message_id, delivery_id, f"cfdel_{uuid4().hex}", task.task_id,
                      execution.execution_id, task.target_agent_id, source.endpoint_id, source.native_session_id,
                      diagnostics, outbound, _review_code_hash(review_code), now, now))
                return self._get(db, report_id)
        report = await asyncio.to_thread(write)
        # A crash before linkage can replay acceptance with the same Report ID.
        await self.messages.mark_processed(report.message_id)
        return report

    async def _matches_identity(self, endpoint_id: c.EndpointId, native_session_id: c.NativeSessionId) -> bool:
        identity = await self.identities.get_identity(endpoint_id)
        return (
            identity is not None and identity.endpoint_id == endpoint_id and identity.provider == "chatgpt_web"
            and identity.native_session_id == native_session_id
        )

    async def send_report(self, report_id: c.ReportId, sender: ReportSender) -> TaskReport:
        def claim():
            with self.tasks.transaction() as db:
                self._get(db, report_id)
                changed = db.execute("""
                    UPDATE task_report_returns SET state='sending', updated_at=?, error_json=NULL
                    WHERE report_id=? AND state='pending'
                """, (utc_now(), report_id))
                return self._get(db, report_id), bool(changed.rowcount)
        report, acquired = await asyncio.to_thread(claim)
        if not acquired:
            return report

        try:
            endpoint_id = report.delivery.target_id
            try:
                matches = await self._matches_identity(endpoint_id, report.native_session_id)
            except ContextFlowError:
                matches = False
            if not matches:
                return await self._finish_send(report_id, _error_outcome(
                    c.DeliveryState.PENDING, "REPORT_ENDPOINT_UNAVAILABLE", "original Supervisor binding cannot be verified",
                ))
            try:
                outcome = await sender.send_message_to_endpoint(endpoint_id, report.native_session_id, report.outbound_text)
            except Exception:
                outcome = _error_outcome(c.DeliveryState.UNKNOWN, "REPORT_SEND_UNKNOWN", "send outcome could not be verified")
            return await self._finish_send(report_id, outcome)
        except asyncio.CancelledError:
            await self.recover_report_send(report_id)
            raise

    async def _finish_send(self, report_id: c.ReportId, outcome: c.DeliveryOutcome) -> TaskReport:
        def write():
            with self.tasks.transaction() as db:
                db.execute("""
                    UPDATE task_report_returns SET state=?, error_json=?, evidence_json=?, updated_at=?
                    WHERE report_id=? AND state='sending'
                """, (outcome.state.value, json.dumps(outcome.error.to_dict()) if outcome.error else None,
                      json.dumps(thaw_json(outcome.evidence)), utc_now(), report_id))
                return self._get(db, report_id)
        return await asyncio.to_thread(write)

    async def recover_report_send(self, report_id: c.ReportId) -> TaskReport:
        """After the original sender stops, settle an interrupted send as unknown."""
        return await self._finish_send(report_id, _error_outcome(
            c.DeliveryState.UNKNOWN, "REPORT_SEND_INTERRUPTED", "previous send has no confirmed receipt",
        ))

    async def submit_review(
        self, *, report_id: c.ReportId, task_id: c.TaskId, endpoint_id: c.EndpointId,
        native_session_id: c.NativeSessionId, review_code: str, decision: ReviewDecision, note: str,
    ) -> TaskReport:
        """Accept an explicit decision from the authenticated Supervisor control channel."""
        if not await self._matches_identity(endpoint_id, native_session_id):
            raise ContextFlowError("REPORT_REVIEW_DENIED", "review requires the original Supervisor")

        def write():
            with self.tasks.transaction() as db:
                report = self._get(db, report_id)
                saved_hash = db.execute(
                    "SELECT review_code_hash FROM task_report_returns WHERE report_id=?", (report_id,),
                ).fetchone()[0]
                if (report.task_id != task_id or report.delivery.target_id != endpoint_id
                        or report.native_session_id != native_session_id
                        or not secrets.compare_digest(saved_hash, _review_code_hash(review_code))):
                    raise ContextFlowError("REPORT_REVIEW_DENIED", "review requires the original Supervisor")
                if report.delivery.state is not c.DeliveryState.CONFIRMED:
                    raise ContextFlowError("REPORT_REVIEW_NOT_READY", "report delivery is not confirmed")
                if report.review_decision is not None:
                    if (report.review_decision, report.review_note) != (decision, note):
                        raise ContextFlowError("REPORT_REVIEW_CONFLICT", "review decision is already recorded")
                    return report
                db.execute("""
                    UPDATE task_report_returns SET review_decision=?, review_note=?, reviewed_at=?
                    WHERE report_id=?
                """, (decision.value, note, utc_now(), report_id))
                return self._get(db, report_id)
        return await asyncio.to_thread(write)
