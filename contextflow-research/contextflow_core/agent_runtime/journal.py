"""Local execution journal for adapter-level idempotency.

Boundary: this journal is a *safety net*, not the authoritative Delivery store.
Core's future Delivery Store remains the single source of truth for whether a
Task may be re-delivered. The journal only prevents this adapter (including
after a restart) from launching a second execution for the same caller_ref.

Persistence rules:

* one JSON file per adapter journal directory, atomically replaced on write;
* only execution metadata is stored (never credentials or full output);
* on load, any non-terminal record is conservatively rewritten as ``unknown``
  because the adapter cannot prove the old process is still alive;
* an ``unknown`` record blocks re-execution for the same caller_ref.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .contracts import ExecutionState, is_terminal, utc_now
from .errors import IdempotencyConflict

JOURNAL_VERSION = 1


@dataclass(frozen=True)
class JournalRecord:
    execution_id: str
    runtime: str
    caller_ref: str
    fingerprint: str
    state: ExecutionState
    created_at: str
    updated_at: str
    native_session_id: str | None = None
    pid: int | None = None
    resumable: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "runtime": self.runtime,
            "caller_ref": self.caller_ref,
            "fingerprint": self.fingerprint,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "native_session_id": self.native_session_id,
            "pid": self.pid,
            "resumable": self.resumable,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "JournalRecord":
        return cls(
            execution_id=str(raw["execution_id"]),
            runtime=str(raw["runtime"]),
            caller_ref=str(raw["caller_ref"]),
            fingerprint=str(raw["fingerprint"]),
            state=ExecutionState(raw["state"]),
            created_at=str(raw["created_at"]),
            updated_at=str(raw["updated_at"]),
            native_session_id=raw.get("native_session_id"),
            pid=raw.get("pid"),
            resumable=bool(raw.get("resumable", False)),
            note=str(raw.get("note", "")),
        )


class ExecutionJournal:
    def __init__(self, directory: str | Path, filename: str = "executions.json") -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / filename
        self._lock = threading.RLock()
        self._records: dict[str, JournalRecord] = {}
        self._load_and_recover()

    # -- persistence ------------------------------------------------------
    def _load_and_recover(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A corrupt journal must not silently allow re-execution.
            self._records = {}
            return
        if not isinstance(raw, dict) or raw.get("version") != JOURNAL_VERSION:
            self._records = {}
            return
        records: dict[str, JournalRecord] = {}
        for item in raw.get("executions", []):
            try:
                record = JournalRecord.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
            records[record.execution_id] = record
        self._records = records
        self._recover_after_restart()

    def _recover_after_restart(self) -> None:
        changed = False
        for execution_id, record in list(self._records.items()):
            if not is_terminal(record.state):
                self._records[execution_id] = replace(
                    record,
                    state=ExecutionState.UNKNOWN,
                    updated_at=utc_now(),
                    note="recovered_after_adapter_restart: outcome not provable",
                )
                changed = True
        if changed:
            self._save()

    def _save(self) -> None:
        payload = {
            "version": JOURNAL_VERSION,
            "executions": [record.to_dict() for record in self._records.values()],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # -- public API -------------------------------------------------------
    def lookup(self, caller_ref: str, fingerprint: str) -> JournalRecord | None:
        with self._lock:
            for record in self._records.values():
                if record.caller_ref != caller_ref:
                    continue
                if record.fingerprint != fingerprint:
                    raise IdempotencyConflict(
                        "caller_ref already used for a different execution request",
                        details={
                            "caller_ref": caller_ref,
                            "existing_execution_id": record.execution_id,
                        },
                    )
                return record
            return None

    def register(self, record: JournalRecord) -> None:
        with self._lock:
            if record.execution_id in self._records:
                raise IdempotencyConflict(
                    "execution id already registered",
                    details={"execution_id": record.execution_id},
                )
            self._records[record.execution_id] = record
            self._save()

    def update(self, execution_id: str, **fields: Any) -> JournalRecord:
        with self._lock:
            record = self._records.get(execution_id)
            if record is None:
                raise KeyError(execution_id)
            updated = replace(record, updated_at=utc_now(), **fields)
            self._records[execution_id] = updated
            self._save()
            return updated

    def get(self, execution_id: str) -> JournalRecord | None:
        with self._lock:
            return self._records.get(execution_id)

    def all(self) -> tuple[JournalRecord, ...]:
        with self._lock:
            return tuple(self._records.values())
