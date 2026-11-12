"""Core-side association between a TaskDelivery and its one Agent execution."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass

from . import contracts as c
from .agent_runtime import ExecutionHandle, ExecutionResult, ExecutionSnapshot, ExecutionState, FailureKind, utc_now
from .errors import ContextFlowError
from .task_store import SQLiteTaskRepository


_SCHEMA = """CREATE TABLE IF NOT EXISTS task_executions (
    delivery_id TEXT PRIMARY KEY NOT NULL REFERENCES task_deliveries(delivery_id),
    runtime TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    execution_id TEXT,
    native_session_id TEXT,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    handle_created_at TEXT,
    result_json TEXT,
    UNIQUE (runtime, execution_id)
)"""
_INLINE_OUTPUT_BYTES = 64 * 1024


@dataclass(frozen=True)
class TaskExecution:
    task_id: c.TaskId
    delivery_id: c.DeliveryId
    runtime: str
    request_fingerprint: str
    execution_id: str | None
    native_session_id: str | None
    state: ExecutionState
    created_at: str
    updated_at: str
    handle_created_at: str | None
    error: c.ErrorInfo | None
    result: dict | None

    @property
    def has_final_result(self) -> bool:
        return self.result is not None and self.state in (
            ExecutionState.FINISHED, ExecutionState.FAILED, ExecutionState.CANCELLED,
        )


class SQLiteExecutionRepository:
    def __init__(self, tasks: SQLiteTaskRepository) -> None:
        self.tasks = tasks
        with self.tasks.transaction() as db:
            db.execute(_SCHEMA)

    @staticmethod
    def _get(db: sqlite3.Connection, delivery_id: c.DeliveryId) -> TaskExecution | None:
        row = db.execute("""
            SELECT e.*, d.task_id, d.error_json FROM task_executions e
            JOIN task_deliveries d ON d.delivery_id=e.delivery_id
            WHERE e.delivery_id=?
        """, (delivery_id,)).fetchone()
        if row is None:
            return None
        return TaskExecution(
            task_id=c.TaskId(row["task_id"]), delivery_id=c.DeliveryId(row["delivery_id"]),
            runtime=row["runtime"], request_fingerprint=row["request_fingerprint"],
            execution_id=row["execution_id"], native_session_id=row["native_session_id"],
            state=ExecutionState(row["state"]), created_at=row["created_at"], updated_at=row["updated_at"],
            handle_created_at=row["handle_created_at"],
            error=c.ErrorInfo(**json.loads(row["error_json"])) if row["error_json"] else None,
            result=json.loads(row["result_json"]) if row["result_json"] else None,
        )

    async def get(self, delivery_id: c.DeliveryId) -> TaskExecution | None:
        def read():
            with self.tasks.connection() as db:
                return self._get(db, delivery_id)
        return await asyncio.to_thread(read)

    async def claim(self, delivery: c.TaskDelivery, runtime: str, fingerprint: str) -> bool:
        """Durably reserve one start before performing any Agent I/O."""
        def write():
            with self.tasks.transaction() as db:
                if self._get(db, delivery.delivery_id) is not None:
                    return False
                busy = db.execute("""
                    SELECT 1 FROM task_executions e JOIN task_deliveries d ON d.delivery_id=e.delivery_id
                    WHERE d.target_agent_id=? AND e.state IN ('pending', 'running') LIMIT 1
                """, (delivery.target_id,)).fetchone()
                if busy is not None:
                    raise ContextFlowError("TASK_RUNTIME_BUSY", "Agent already has an active execution")
                now = utc_now()
                changed = db.execute("""
                    UPDATE task_deliveries SET state='sending', updated_at=?
                    WHERE delivery_id=? AND state='pending'
                """, (now, delivery.delivery_id))
                if not changed.rowcount:
                    raise ContextFlowError("TASK_DELIVERY_NOT_PENDING", "only pending deliveries can be started")
                db.execute("""
                    INSERT INTO task_executions (delivery_id, runtime, request_fingerprint, state, created_at, updated_at)
                    VALUES (?, ?, ?, 'pending', ?, ?)
                """, (delivery.delivery_id, runtime, fingerprint, now, now))
                return True
        return await asyncio.to_thread(write)

    async def record_handle(self, delivery_id: c.DeliveryId, handle: ExecutionHandle) -> TaskExecution:
        def write():
            with self.tasks.transaction() as db:
                now = utc_now()
                changed = db.execute("""
                    UPDATE task_executions
                    SET execution_id=?, native_session_id=?, state=?, handle_created_at=?, updated_at=?
                    WHERE delivery_id=? AND execution_id IS NULL AND state IN ('pending', 'unknown')
                """, (
                    handle.execution_id, handle.native_session_id, handle.state.value, handle.created_at, now, delivery_id,
                ))
                if changed.rowcount:
                    evidence = {"execution_id": handle.execution_id, "runtime": handle.runtime}
                    db.execute("""
                        UPDATE task_deliveries SET state='confirmed', evidence_json=?, error_json=NULL, updated_at=?
                        WHERE delivery_id=?
                    """, (json.dumps(evidence), now, delivery_id))
                    db.execute("""
                        UPDATE tasks SET state='dispatched', updated_at=?
                        WHERE task_id=(SELECT task_id FROM task_deliveries WHERE delivery_id=?)
                          AND state='pending_delivery'
                    """, (now, delivery_id))
                return self._get(db, delivery_id)
        return await asyncio.to_thread(write)

    async def record_start_error(
        self, delivery_id: c.DeliveryId, error: c.ErrorInfo, *, rejected: bool = False,
    ) -> TaskExecution:
        def write():
            with self.tasks.transaction() as db:
                state, now = ("failed" if rejected else "unknown"), utc_now()
                changed = db.execute("""
                    UPDATE task_executions SET state=?, updated_at=?
                    WHERE delivery_id=? AND execution_id IS NULL AND state IN ('pending', 'unknown')
                """, (state, now, delivery_id))
                if changed.rowcount:
                    db.execute("""
                        UPDATE task_deliveries SET state=?, error_json=?, updated_at=? WHERE delivery_id=?
                    """, (state, json.dumps(error.to_dict()), now, delivery_id))
                return self._get(db, delivery_id)
        return await asyncio.to_thread(write)

    async def record_observation(
        self, delivery_id: c.DeliveryId, snapshot: ExecutionSnapshot, result: ExecutionResult | None,
    ) -> TaskExecution:
        def write():
            with self.tasks.transaction() as db:
                current = self._get(db, delivery_id)
                if current.has_final_result:
                    return current
                if (current.state is ExecutionState.UNKNOWN and snapshot.state is ExecutionState.UNKNOWN
                        and current.result is not None and not snapshot.result_complete
                        and not snapshot.output_text and not (result and result.final_text)
                        and not snapshot.usage):
                    # A restarted Adapter may have only the Journal's state, without the saved output.
                    return current
                result_json = (
                    json.dumps(self._result_document(delivery_id, result, current), ensure_ascii=False)
                    if result is not None else None
                )
                db.execute("""
                    UPDATE task_executions SET state=?, native_session_id=COALESCE(?, native_session_id),
                        result_json=?, updated_at=?
                    WHERE delivery_id=?
                """, (snapshot.state.value, snapshot.native_session_id, result_json, utc_now(), delivery_id))
                return self._get(db, delivery_id)
        return await asyncio.to_thread(write)

    def _result_document(self, delivery_id: c.DeliveryId, result: ExecutionResult, current: TaskExecution) -> dict:
        snapshot = result.snapshot
        data = snapshot.to_dict()
        failure_kind = result.failure_kind.value
        if (snapshot.state is ExecutionState.UNKNOWN and current.result
                and result.failure_kind in (FailureKind.NONE, FailureKind.UNKNOWN)):
            failure_kind = current.result["failure_kind"]
        output = result.final_text if result.final_text is not None else snapshot.output_text
        output_ref = None
        if output is not None and len(output.encode("utf-8")) > _INLINE_OUTPUT_BYTES:
            directory = self.tasks.path.parent / f"{self.tasks.path.name}.execution-results"
            directory.mkdir(exist_ok=True)
            path = directory / f"{delivery_id}.txt"
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(output.encode("utf-8"))
            temporary.replace(path)
            output_ref = path.relative_to(self.tasks.path.parent).as_posix()
            output = None
        return {
            "state": snapshot.state.value,
            "failure_kind": failure_kind,
            "native_session_id": snapshot.native_session_id or current.native_session_id,
            "started_at": snapshot.started_at,
            "ended_at": snapshot.ended_at,
            "error": data["error"],
            "usage": data["usage"],
            "result_complete": snapshot.result_complete,
            "side_effects_possible": snapshot.side_effects_possible,
            "final_text": output,
            "output_ref": output_ref,
        }
