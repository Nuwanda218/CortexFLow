"""Owned SQLite schema lifecycle for the Message Store and Parser jobs.

Normal Core components may create a brand-new v2 database or validate an
existing v2 database.  Upgrading a known v1 database is deliberately exposed
as a separate maintenance operation; opening a Store never migrates data.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import math
import sqlite3

from .errors import ContextFlowError

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2
V1_SCHEMA_VERSION = 1

MESSAGE_COLUMNS = (
    "message_id", "kind", "submission_id", "source_agent_id", "report_id",
    "endpoint_id", "native_session_id", "native_message_id", "envelope_json",
    "fingerprint", "received_at", "processing_state", "processing_error_json",
)

MESSAGE_SCHEMA = """
CREATE TABLE messages (
    message_id TEXT PRIMARY KEY NOT NULL
        CHECK(length(message_id) = 38 AND substr(message_id, 1, 6) = 'cfmsg_'
              AND substr(message_id, 7) NOT GLOB '*[^0-9a-f]*'),
    kind TEXT NOT NULL CHECK(kind IN ('supervisor_instruction', 'agent_report')),
    submission_id TEXT,
    source_agent_id TEXT,
    report_id TEXT,
    endpoint_id TEXT,
    native_session_id TEXT,
    native_message_id TEXT,
    envelope_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL
        CHECK(length(fingerprint) = 64 AND fingerprint NOT GLOB '*[^0-9a-f]*'),
    received_at TEXT NOT NULL,
    processing_state TEXT NOT NULL DEFAULT 'pending'
        CHECK(processing_state IN ('pending', 'processed', 'blocked')),
    processing_error_json TEXT,
    CHECK((processing_state = 'blocked' AND processing_error_json IS NOT NULL)
          OR (processing_state = 'processed' AND processing_error_json IS NULL)
          OR processing_state = 'pending'),
    CHECK((kind = 'supervisor_instruction' AND submission_id IS NOT NULL
           AND endpoint_id IS NOT NULL AND native_session_id IS NOT NULL
           AND native_message_id IS NOT NULL AND source_agent_id IS NULL AND report_id IS NULL)
          OR (kind = 'agent_report' AND submission_id IS NULL
           AND endpoint_id IS NULL AND native_session_id IS NULL AND native_message_id IS NULL
           AND source_agent_id IS NOT NULL AND report_id IS NOT NULL))
)
"""

MESSAGE_INDEXES = {
    "messages_supervisor_submission_unique": (
        "CREATE UNIQUE INDEX messages_supervisor_submission_unique ON messages(submission_id) WHERE kind = 'supervisor_instruction'",
        ("submission_id",), True,
    ),
    "messages_agent_report_unique": (
        "CREATE UNIQUE INDEX messages_agent_report_unique ON messages(source_agent_id, report_id) WHERE kind = 'agent_report'",
        ("source_agent_id", "report_id"), True,
    ),
    "messages_pending": (
        "CREATE INDEX messages_pending ON messages(kind, processing_state, received_at, message_id)",
        ("kind", "processing_state", "received_at", "message_id"), False,
    ),
}

PARSE_JOB_COLUMNS = (
    "message_id", "job_state", "claim_generation", "lease_owner",
    "claim_request_id", "lease_expires_at_ms", "attempt_count",
    "last_attempt_generation", "max_attempts", "next_attempt_at_ms",
    "last_error_json", "result_status", "instructions_json",
    "result_error_json", "intent_fingerprint", "created_at_ms",
    "updated_at_ms", "completed_at_ms",
)

PARSE_JOB_SCHEMA = """
CREATE TABLE message_parse_jobs (
    message_id TEXT PRIMARY KEY NOT NULL REFERENCES messages(message_id),
    job_state TEXT NOT NULL CHECK(job_state IN ('ready', 'processing', 'retry_wait', 'completed')),
    claim_generation INTEGER NOT NULL
        CHECK(typeof(claim_generation) = 'integer' AND claim_generation >= 0),
    lease_owner TEXT,
    claim_request_id TEXT,
    lease_expires_at_ms INTEGER,
    attempt_count INTEGER NOT NULL
        CHECK(typeof(attempt_count) = 'integer' AND attempt_count >= 0),
    last_attempt_generation INTEGER,
    max_attempts INTEGER NOT NULL
        CHECK(typeof(max_attempts) = 'integer' AND max_attempts >= 1),
    next_attempt_at_ms INTEGER,
    last_error_json TEXT,
    result_status TEXT CHECK(result_status IN ('parsed', 'unresolved', 'failed')),
    instructions_json TEXT,
    result_error_json TEXT,
    intent_fingerprint TEXT,
    created_at_ms INTEGER NOT NULL
        CHECK(typeof(created_at_ms) = 'integer' AND created_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL
        CHECK(typeof(updated_at_ms) = 'integer' AND updated_at_ms >= 0),
    completed_at_ms INTEGER,
    CHECK(lease_owner IS NULL OR (typeof(lease_owner) = 'text' AND length(trim(lease_owner)) > 0)),
    CHECK(claim_request_id IS NULL OR (typeof(claim_request_id) = 'text' AND length(trim(claim_request_id)) > 0)),
    CHECK(lease_expires_at_ms IS NULL OR (typeof(lease_expires_at_ms) = 'integer' AND lease_expires_at_ms >= 0)),
    CHECK(last_attempt_generation IS NULL OR (typeof(last_attempt_generation) = 'integer'
          AND last_attempt_generation >= 1 AND last_attempt_generation <= claim_generation)),
    CHECK(next_attempt_at_ms IS NULL OR (typeof(next_attempt_at_ms) = 'integer' AND next_attempt_at_ms >= 0)),
    CHECK(completed_at_ms IS NULL OR (typeof(completed_at_ms) = 'integer' AND completed_at_ms >= 0)),
    CHECK(attempt_count <= max_attempts AND attempt_count <= claim_generation),
    CHECK((attempt_count = 0 AND last_attempt_generation IS NULL)
          OR (attempt_count > 0 AND last_attempt_generation IS NOT NULL)),
    CHECK(intent_fingerprint IS NULL OR (length(intent_fingerprint) = 64
          AND intent_fingerprint NOT GLOB '*[^0-9a-f]*')),
    CHECK(CASE job_state
        WHEN 'ready' THEN claim_generation = 0 AND attempt_count = 0
             AND lease_owner IS NULL AND claim_request_id IS NULL
             AND lease_expires_at_ms IS NULL AND next_attempt_at_ms IS NULL
             AND result_status IS NULL AND instructions_json IS NULL
             AND result_error_json IS NULL AND intent_fingerprint IS NULL
             AND completed_at_ms IS NULL
        WHEN 'processing' THEN claim_generation > 0
             AND lease_owner IS NOT NULL AND claim_request_id IS NOT NULL
             AND lease_expires_at_ms IS NOT NULL AND next_attempt_at_ms IS NULL
             AND result_status IS NULL AND instructions_json IS NULL
             AND result_error_json IS NULL AND intent_fingerprint IS NULL
             AND completed_at_ms IS NULL
        WHEN 'retry_wait' THEN claim_generation > 0
             AND lease_owner IS NULL AND claim_request_id IS NULL
             AND lease_expires_at_ms IS NULL AND next_attempt_at_ms IS NOT NULL
             AND result_status IS NULL AND instructions_json IS NULL
             AND result_error_json IS NULL AND intent_fingerprint IS NULL
             AND completed_at_ms IS NULL
        WHEN 'completed' THEN claim_generation > 0
             AND lease_owner IS NULL AND claim_request_id IS NULL
             AND lease_expires_at_ms IS NULL AND next_attempt_at_ms IS NULL
             AND result_status IS NOT NULL AND instructions_json IS NOT NULL
             AND completed_at_ms IS NOT NULL
             AND ((result_status = 'parsed' AND result_error_json IS NULL
                   AND intent_fingerprint IS NOT NULL)
                  OR (result_status IN ('unresolved', 'failed')
                      AND result_error_json IS NOT NULL
                      AND intent_fingerprint IS NULL))
        ELSE 0
    END)
)
"""

PARSE_JOB_INDEXES = {
    "message_parse_jobs_claim_request_unique": (
        "CREATE UNIQUE INDEX message_parse_jobs_claim_request_unique ON message_parse_jobs(claim_request_id) WHERE claim_request_id IS NOT NULL",
        ("claim_request_id",), True,
    ),
    "message_parse_jobs_due": (
        "CREATE INDEX message_parse_jobs_due ON message_parse_jobs(job_state, next_attempt_at_ms, message_id)",
        ("job_state", "next_attempt_at_ms", "message_id"), False,
    ),
    "message_parse_jobs_lease": (
        "CREATE INDEX message_parse_jobs_lease ON message_parse_jobs(job_state, lease_expires_at_ms, message_id)",
        ("job_state", "lease_expires_at_ms", "message_id"), False,
    ),
}


def schema_error() -> ContextFlowError:
    return ContextFlowError(
        "MESSAGE_STORE_SCHEMA_ERROR",
        "unsupported or inconsistent message database schema",
    )


def migration_required() -> ContextFlowError:
    return ContextFlowError(
        "MESSAGE_STORE_MIGRATION_REQUIRED",
        "message database schema v1 requires an explicit v2 maintenance migration",
    )


def _duration(value: float, field: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ContextFlowError(
            "INVALID_ARGUMENT", f"invalid {field}: must be a finite number",
            details={"field": field},
        )
    if value < 0 or (value == 0 and not allow_zero):
        reason = "must be nonnegative" if allow_zero else "must be positive"
        raise ContextFlowError(
            "INVALID_ARGUMENT", f"invalid {field}: {reason}",
            details={"field": field},
        )
    return float(value)


def _objects(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'"
        )
    }


def _validate_table(
    connection: sqlite3.Connection,
    name: str,
    statement: str,
    columns: tuple[str, ...],
) -> None:
    table = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    if table is None or table[0].strip() != statement.strip():
        raise schema_error()
    actual_columns = tuple(row[1] for row in connection.execute(f"PRAGMA table_info({name})"))
    if actual_columns != columns:
        raise schema_error()
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' AND lower(tbl_name)=lower(?) LIMIT 1",
        (name,),
    ).fetchone() is not None:
        raise schema_error()


def _validate_indexes(
    connection: sqlite3.Connection,
    table_name: str,
    expected: dict[str, tuple[str, tuple[str, ...], bool]],
) -> None:
    indexes = {row[1]: row for row in connection.execute(f"PRAGMA index_list({table_name})")}
    for name, (statement, columns, unique) in expected.items():
        index = indexes.get(name)
        definition = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)
        ).fetchone()
        if (
            index is None
            or bool(index[2]) != unique
            or definition is None
            or definition[0].strip() != statement.strip()
        ):
            raise schema_error()
        actual = tuple(row[2] for row in connection.execute(f"PRAGMA index_info({name})"))
        if actual != columns:
            raise schema_error()


def validate_v1(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone()[0] != V1_SCHEMA_VERSION:
        raise schema_error()
    _validate_table(connection, "messages", MESSAGE_SCHEMA, MESSAGE_COLUMNS)
    _validate_indexes(connection, "messages", MESSAGE_INDEXES)
    if "message_parse_jobs" in _objects(connection):
        raise schema_error()


def validate_v2(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        raise schema_error()
    _validate_table(connection, "messages", MESSAGE_SCHEMA, MESSAGE_COLUMNS)
    _validate_indexes(connection, "messages", MESSAGE_INDEXES)
    _validate_table(
        connection, "message_parse_jobs", PARSE_JOB_SCHEMA, PARSE_JOB_COLUMNS
    )
    _validate_indexes(connection, "message_parse_jobs", PARSE_JOB_INDEXES)


def configure_connection(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")


def _create_v2(connection: sqlite3.Connection) -> None:
    connection.execute(MESSAGE_SCHEMA)
    for statement, _, _ in MESSAGE_INDEXES.values():
        connection.execute(statement)
    connection.execute(PARSE_JOB_SCHEMA)
    for statement, _, _ in PARSE_JOB_INDEXES.values():
        connection.execute(statement)
    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def ensure_schema_v2(connection: sqlite3.Connection) -> None:
    """Create an empty v2 database or validate v2; never migrate v1."""

    version = connection.execute("PRAGMA user_version").fetchone()[0]
    objects = _objects(connection)
    if version == V1_SCHEMA_VERSION:
        validate_v1(connection)
        raise migration_required()
    if version == SCHEMA_VERSION:
        validate_v2(connection)
    elif version != 0 or objects:
        raise schema_error()

    configure_connection(connection)
    connection.execute("BEGIN IMMEDIATE")
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            if _objects(connection):
                raise schema_error()
            _create_v2(connection)
        elif version == V1_SCHEMA_VERSION:
            validate_v1(connection)
            raise migration_required()
        elif version != SCHEMA_VERSION:
            raise schema_error()
        validate_v2(connection)
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def migrate_path_to_v2(
    path: str | Path,
    *,
    busy_timeout: float = 5.0,
) -> bool:
    """Explicitly upgrade a known v1 file. Return True only when upgraded."""

    if str(path) == ":memory:" or str(path).startswith("file:"):
        raise ContextFlowError(
            "INVALID_ARGUMENT", "invalid path: must name a persistent database file",
            details={"field": "path"},
        )
    database = Path(path).expanduser()
    if not database.is_file():
        raise ContextFlowError(
            "MESSAGE_STORE_OPEN_FAILED", "cannot migrate a missing message database",
            details={"operation": "migrate"},
        )
    timeout = _duration(busy_timeout, "busy_timeout", allow_zero=True)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            database, timeout=timeout, isolation_level=None, check_same_thread=False
        )
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            validate_v2(connection)
            return False
        if version != V1_SCHEMA_VERSION:
            raise schema_error()
        validate_v1(connection)

        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == SCHEMA_VERSION:
                validate_v2(connection)
                connection.execute("COMMIT")
                return False
            if version != V1_SCHEMA_VERSION:
                raise schema_error()
            validate_v1(connection)
            connection.execute(PARSE_JOB_SCHEMA)
            for statement, _, _ in PARSE_JOB_INDEXES.values():
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            validate_v2(connection)
            connection.execute("COMMIT")
            return True
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
    except ContextFlowError:
        raise
    except sqlite3.Error as exc:
        raise ContextFlowError(
            "MESSAGE_STORE_WRITE_FAILED",
            "message database migration failed",
            retryable=isinstance(exc, sqlite3.OperationalError)
            and any(word in str(exc).lower() for word in ("locked", "busy")),
            details={"operation": "migrate", "sqlite_error": type(exc).__name__},
        ) from exc
    finally:
        if connection is not None:
            connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ContextFlow SQLite schema maintenance"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    migrate = subparsers.add_parser("migrate", help="explicitly migrate known v1 to v2")
    migrate.add_argument("--database", required=True)
    migrate.add_argument("--busy-timeout", type=float, default=5.0)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        changed = migrate_path_to_v2(
            args.database, busy_timeout=args.busy_timeout
        )
        logger.info(
            "schema migration status=%s database=%s",
            "migrated" if changed else "already_v2",
            Path(args.database).expanduser(),
        )
        return 0
    except ContextFlowError as exc:
        logger.error("%s: %s", exc.code, exc.message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
