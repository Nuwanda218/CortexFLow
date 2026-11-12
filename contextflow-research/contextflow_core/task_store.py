"""C-04: explicitly materialize a persisted parse as one atomic Task batch."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from . import contracts as c
from .database_schema import ensure_schema_v2
from .parse_jobs import ParseResultReader, StoredParseResult


_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS task_batches (
        source_message_id TEXT PRIMARY KEY NOT NULL REFERENCES message_parse_jobs(message_id),
        intent_fingerprint TEXT NOT NULL,
        parse_completed_at_ms INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS tasks (
        task_id TEXT PRIMARY KEY NOT NULL,
        source_message_id TEXT NOT NULL REFERENCES task_batches(source_message_id),
        instruction_index INTEGER NOT NULL,
        target_agent_id TEXT NOT NULL,
        content TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('pending_delivery', 'dispatched', 'completed', 'failed')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        final_report_message_id TEXT REFERENCES messages(message_id),
        parent_task_id TEXT REFERENCES tasks(task_id),
        requester_agent_id TEXT,
        origin_report_id TEXT,
        UNIQUE (source_message_id, instruction_index)
    )""",
    """CREATE TABLE IF NOT EXISTS task_deliveries (
        delivery_id TEXT PRIMARY KEY NOT NULL,
        task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
        target_agent_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('pending', 'sending', 'confirmed', 'failed', 'unknown')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        error_json TEXT,
        evidence_json TEXT NOT NULL DEFAULT '{}'
    )""",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _rejected(code: str, message: str) -> c.CreateTasksResult:
    return c.CreateTasksResult(
        status=c.CreateTasksStatus.REJECTED, error=c.ErrorInfo(code=code, message=message),
    )


def _task(row: sqlite3.Row) -> c.Task:
    return c.Task(
        task_id=c.TaskId(row["task_id"]), source_message_id=c.MessageId(row["source_message_id"]),
        instruction_index=row["instruction_index"], target_agent_id=c.AgentId(row["target_agent_id"]),
        content=row["content"], state=c.TaskState(row["state"]),
        created_at=row["created_at"], updated_at=row["updated_at"],
        final_report_message_id=c.MessageId(row["final_report_message_id"]) if row["final_report_message_id"] else None,
        parent_task_id=c.TaskId(row["parent_task_id"]) if row["parent_task_id"] else None,
        requester_agent_id=c.AgentId(row["requester_agent_id"]) if row["requester_agent_id"] else None,
        origin_report_id=c.ReportId(row["origin_report_id"]) if row["origin_report_id"] else None,
    )


def _delivery(row: sqlite3.Row) -> c.TaskDelivery:
    return c.TaskDelivery(
        delivery_id=c.DeliveryId(row["delivery_id"]), subject_id=c.TaskId(row["task_id"]),
        target_id=c.AgentId(row["target_agent_id"]), state=c.DeliveryState(row["state"]),
        created_at=row["created_at"], updated_at=row["updated_at"],
        error=c.ErrorInfo(**json.loads(row["error_json"])) if row["error_json"] else None,
        evidence=json.loads(row["evidence_json"]),
    )


class SQLiteTaskRepository:
    """Owns the additive Task tables in the existing Stage B database."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        with self.connection() as db:
            ensure_schema_v2(db)
        with self.transaction() as db:
            for statement in _SCHEMA:
                db.execute(statement)

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            yield db

    @contextmanager
    def transaction(self):
        with self.connection() as db, db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    async def get_task(self, task_id: c.TaskId) -> c.Task | None:
        def read():
            with self.connection() as db:
                row = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
                return _task(row) if row is not None else None
        return await asyncio.to_thread(read)

    async def get_tasks_for_message(self, message_id: c.MessageId) -> tuple[c.Task, ...]:
        def read():
            with self.connection() as db:
                return self._tasks_for_message(db, message_id)
        return await asyncio.to_thread(read)

    async def get_delivery(self, delivery_id: c.DeliveryId) -> c.TaskDelivery | None:
        def read():
            with self.connection() as db:
                row = db.execute("SELECT * FROM task_deliveries WHERE delivery_id=?", (delivery_id,)).fetchone()
                return _delivery(row) if row is not None else None
        return await asyncio.to_thread(read)

    @staticmethod
    def _tasks_for_message(db, source):
        rows = db.execute(
            "SELECT * FROM tasks WHERE source_message_id=? ORDER BY instruction_index", (source,),
        )
        return tuple(_task(row) for row in rows)

    def _batch(self, db, source, fingerprint, status) -> c.CreateTasksResult | None:
        batch = db.execute(
            "SELECT intent_fingerprint FROM task_batches WHERE source_message_id=?", (source,),
        ).fetchone()
        if batch is None:
            return None
        if batch["intent_fingerprint"] != fingerprint:
            return c.CreateTasksResult(
                status=c.CreateTasksStatus.CONFLICT,
                error=c.ErrorInfo(code="TASK_BATCH_CONFLICT", message="source already has a different task batch"),
            )
        tasks = self._tasks_for_message(db, source)
        rows = db.execute("""
            SELECT d.* FROM task_deliveries d JOIN tasks t ON t.task_id=d.task_id
            WHERE t.source_message_id=? ORDER BY t.instruction_index
        """, (source,))
        return c.CreateTasksResult(
            status=status, tasks=tasks, deliveries=tuple(_delivery(row) for row in rows),
            intent_fingerprint=fingerprint,
        )

    async def find_batch(self, source: c.MessageId, fingerprint: str) -> c.CreateTasksResult | None:
        def read():
            with self.connection() as db, db:
                db.execute("BEGIN")
                return self._batch(db, source, fingerprint, c.CreateTasksStatus.DUPLICATE)
        return await asyncio.to_thread(read)

    async def create_batch(self, stored: StoredParseResult, targets: tuple[c.AgentId, ...]) -> c.CreateTasksResult:
        source, parsed = stored.message_id, stored.result
        fingerprint = parsed.intent_fingerprint

        def write():
            with self.transaction() as db:
                existing = self._batch(db, source, fingerprint, c.CreateTasksStatus.DUPLICATE)
                if existing is not None:
                    return existing
                row = db.execute("""
                    SELECT m.processing_state, j.intent_fingerprint, j.completed_at_ms
                    FROM messages m JOIN message_parse_jobs j ON m.message_id=j.message_id
                    WHERE m.message_id=? AND m.kind='supervisor_instruction'
                      AND j.job_state='completed' AND j.result_status='parsed'
                """, (source,)).fetchone()
                if (row is None or row["processing_state"] != "pending" or
                    row["intent_fingerprint"] != fingerprint or row["completed_at_ms"] != stored.completed_at_ms):
                    return _rejected("TASK_SOURCE_NOT_READY", "source no longer matches the persisted parse")

                now = _now()
                db.execute("INSERT INTO task_batches VALUES (?, ?, ?, ?)", (source, fingerprint, stored.completed_at_ms, now))
                for index, (instruction, target) in enumerate(zip(parsed.instructions, targets, strict=True)):
                    task_id = f"cftask_{uuid4().hex}"
                    db.execute("""
                        INSERT INTO tasks (task_id, source_message_id, instruction_index, target_agent_id,
                                           content, state, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, 'pending_delivery', ?, ?)
                    """, (task_id, source, index, target, instruction.content, now, now))
                    db.execute("""
                        INSERT INTO task_deliveries (delivery_id, task_id, target_agent_id, state, created_at, updated_at)
                        VALUES (?, ?, ?, 'pending', ?, ?)
                    """, (f"cfdel_{uuid4().hex}", task_id, target, now, now))
                return self._batch(db, source, fingerprint, c.CreateTasksStatus.CREATED)
        return await asyncio.to_thread(write)


class TaskCreationService:
    def __init__(self, tasks: SQLiteTaskRepository, parses: ParseResultReader, agents: c.AgentDirectory) -> None:
        self.tasks = tasks
        self.parses = parses
        self.agents = agents

    async def create_tasks_from_parse_result(self, message_id: c.MessageId) -> c.CreateTasksResult:
        stored = await self.parses.get_final_result(message_id)
        fingerprint = stored.result.intent_fingerprint if stored is not None else None
        if fingerprint is None:
            return _rejected("TASK_PARSE_NOT_READY", "a successfully persisted ParseResult is required")

        existing = await self.tasks.find_batch(message_id, fingerprint)
        if existing is not None:
            return existing
        targets = []
        for instruction in stored.result.instructions:
            agent = await self.agents.resolve(instruction.target_agent)
            if agent is None:
                return _rejected("TASK_TARGET_UNKNOWN", f"unknown Agent: {instruction.target_agent}")
            targets.append(agent.agent_id)
        return await self.tasks.create_batch(stored, tuple(targets))
