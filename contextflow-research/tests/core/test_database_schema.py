import sqlite3
import tempfile
import unittest
from pathlib import Path

from contextflow_core import contracts as c
from contextflow_core.database_schema import (
    SCHEMA_VERSION,
    main as schema_main,
    migrate_path_to_v2,
)
from contextflow_core.endpoint_identity import RegistryEndpointIdentityReader
from contextflow_core.errors import ContextFlowError
from contextflow_core.message_store import SQLiteMessageStore
from contextflow_core.registry import EndpointRegistry
from contextflow_core.submission import canonical_envelope, envelope_fingerprint
from tests.contracts.samples import supervisor_dict


V1_MESSAGES_SCHEMA = """
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

V1_INDEXES = (
    "CREATE UNIQUE INDEX messages_supervisor_submission_unique ON messages(submission_id) WHERE kind = 'supervisor_instruction'",
    "CREATE UNIQUE INDEX messages_agent_report_unique ON messages(source_agent_id, report_id) WHERE kind = 'agent_report'",
    "CREATE INDEX messages_pending ON messages(kind, processing_state, received_at, message_id)",
)


def create_v1(path: Path) -> None:
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(V1_MESSAGES_SCHEMA)
        for statement in V1_INDEXES:
            connection.execute(statement)
        connection.execute("PRAGMA user_version=1")
        connection.execute("COMMIT")
    finally:
        connection.close()


def schema_snapshot(path: Path) -> tuple[int, tuple[tuple[str, str, str], ...], tuple[tuple, ...]]:
    connection = sqlite3.connect(path)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        objects = tuple(connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name NOT GLOB 'sqlite_*' ORDER BY type, name"
        ))
        rows = tuple(connection.execute("SELECT * FROM messages ORDER BY message_id"))
        return version, objects, rows
    finally:
        connection.close()


class DatabaseSchemaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.path = self.root / "messages.sqlite3"
        self.registry_path = self.root / "endpoints.json"
        self.registry = EndpointRegistry(self.registry_path)
        self.binding, _ = self.registry.register("chatgpt_web", "native-session-1")
        self.reader = RegistryEndpointIdentityReader(self.registry_path)
        self.stores: list[SQLiteMessageStore] = []

    async def asyncTearDown(self):
        for store in self.stores:
            await store.close()
        self.directory.cleanup()

    def open_store(self) -> SQLiteMessageStore:
        store = SQLiteMessageStore(self.path, self.reader, busy_timeout=0.2)
        self.stores.append(store)
        return store

    async def test_empty_database_is_created_directly_as_schema_v2(self):
        store = self.open_store()
        raw = supervisor_dict()
        raw["source"]["endpoint_id"] = self.binding.endpoint_id
        accepted = await store.accept_message(c.SupervisorSubmission(envelope=raw))
        self.assertEqual(accepted.status, c.AcceptStatus.ACCEPTED)

        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(SCHEMA_VERSION, 2)
            names = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
            )}
            self.assertIn("messages", names)
            self.assertIn("message_parse_jobs", names)
        finally:
            connection.close()

    async def test_normal_store_open_refuses_v1_without_mutating_it(self):
        create_v1(self.path)
        before = schema_snapshot(self.path)

        with self.assertRaises(ContextFlowError) as caught:
            self.open_store()

        self.assertEqual(caught.exception.code, "MESSAGE_STORE_MIGRATION_REQUIRED")
        self.assertEqual(schema_snapshot(self.path), before)
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        finally:
            connection.close()

    async def test_explicit_v1_migration_preserves_messages_and_existing_index_sql(self):
        create_v1(self.path)
        raw = supervisor_dict()
        raw["submission_id"] = "sub_" + "b" * 32
        raw["source"].update(
            endpoint_id="cfep_" + "c" * 32,
            native_session_id="native-session-1",
            message_id="native-message-1",
        )
        raw["content"]["text"] = "keep me"
        envelope_json = canonical_envelope(raw)
        fingerprint = envelope_fingerprint(raw)
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "cfmsg_" + "a" * 32,
                    "supervisor_instruction",
                    raw["submission_id"],
                    None,
                    None,
                    raw["source"]["endpoint_id"],
                    raw["source"]["native_session_id"],
                    raw["source"]["message_id"],
                    envelope_json,
                    fingerprint,
                    "2026-11-10T00:00:00Z",
                    "pending",
                    None,
                ),
            )
            connection.commit()
            before_rows = tuple(connection.execute("SELECT * FROM messages"))
            before_indexes = dict(connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='index' AND name NOT GLOB 'sqlite_*'"
            ))
        finally:
            connection.close()

        migrated = migrate_path_to_v2(self.path, busy_timeout=0.2)
        self.assertTrue(migrated)

        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(tuple(connection.execute("SELECT * FROM messages")), before_rows)
            after_indexes = dict(connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='index' AND name IN ("
                "'messages_supervisor_submission_unique','messages_agent_report_unique','messages_pending')"
            ))
            self.assertEqual(after_indexes, before_indexes)
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='message_parse_jobs'"
            ).fetchone())
        finally:
            connection.close()

        store = self.open_store()
        restored = await store.get_message(c.MessageId("cfmsg_" + "a" * 32))
        self.assertEqual(restored.envelope.content.text, "keep me")

    async def test_migration_ddl_failure_rolls_back_to_complete_v1(self):
        create_v1(self.path)
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
            connection.execute(
                "CREATE INDEX message_parse_jobs_due ON unrelated(value)"
            )
            connection.commit()
            before_rows = tuple(connection.execute("SELECT * FROM messages"))
        finally:
            connection.close()

        with self.assertRaises(ContextFlowError) as caught:
            migrate_path_to_v2(self.path, busy_timeout=0.2)

        self.assertEqual(caught.exception.code, "MESSAGE_STORE_WRITE_FAILED")
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(tuple(connection.execute("SELECT * FROM messages")), before_rows)
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='message_parse_jobs'"
            ).fetchone())
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name='message_parse_jobs_claim_request_unique'"
            ).fetchone())
        finally:
            connection.close()

    async def test_migration_cli_requires_explicit_existing_database(self):
        with self.assertRaises(SystemExit):
            schema_main([])
        self.assertEqual(schema_main(["migrate", "--database", str(self.path)]), 1)
        create_v1(self.path)
        self.assertEqual(schema_main(["migrate", "--database", str(self.path)]), 0)
        self.assertEqual(schema_main(["migrate", "--database", str(self.path)]), 0)

    async def test_explicit_migration_is_idempotent_for_an_already_valid_v2_database(self):
        self.open_store()
        before = schema_snapshot(self.path)

        migrated = migrate_path_to_v2(self.path, busy_timeout=0.2)

        self.assertFalse(migrated)
        self.assertEqual(schema_snapshot(self.path), before)


if __name__ == "__main__":
    unittest.main()
