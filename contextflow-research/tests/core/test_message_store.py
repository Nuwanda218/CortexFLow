import asyncio
import copy
from contextlib import contextmanager
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from contextflow_core import contracts as c
from contextflow_core.endpoint_identity import RegistryEndpointIdentityReader
from contextflow_core.errors import ContextFlowError
from contextflow_core.message_store import SQLiteMessageStore
from contextflow_core.registry import EndpointRegistry
from contextflow_core.submission import canonical_envelope, envelope_fingerprint
from tests.contracts.samples import report_dict, supervisor_dict


@contextmanager
def database(path):
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


class MessageStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'messages.sqlite3'
        self.registry_path = Path(self.directory.name) / 'endpoints.json'
        self.registry = EndpointRegistry(self.registry_path)
        self.binding, _ = self.registry.register('chatgpt_web', 'native-session-1')
        self.reader = RegistryEndpointIdentityReader(self.registry_path)
        self.store = SQLiteMessageStore(self.path, self.reader, busy_timeout=0.2)
        self.raw = supervisor_dict()
        self.raw['source']['endpoint_id'] = self.binding.endpoint_id

    async def asyncTearDown(self):
        await self.store.close()
        self.directory.cleanup()

    def count(self):
        with database(self.path) as connection:
            return connection.execute('SELECT count(*) FROM messages').fetchone()[0]

    async def accept(self, raw=None, store=None):
        return await (store or self.store).accept_message(c.SupervisorSubmission(envelope=self.raw if raw is None else raw))

    async def test_first_submission_commits_one_contract_message_and_full_source(self):
        self.assertIsInstance(self.store, c.MessageStore)
        result = await self.accept()
        self.assertEqual(result.status, c.AcceptStatus.ACCEPTED)
        self.assertFalse(result.duplicate)
        self.assertRegex(str(result.message_id), r'^cfmsg_[0-9a-f]{32}$')
        self.assertEqual(self.count(), 1)
        message = await self.store.get_message(result.message_id)
        self.assertEqual(message.kind, c.MessageKind.SUPERVISOR_INSTRUCTION)
        self.assertEqual(message.processing_state, c.ProcessingState.PENDING)
        self.assertIsNone(message.processing_error)
        self.assertIsNone(message.source_agent_id)
        self.assertEqual(message.envelope.to_dict(), self.raw)
        with database(self.path) as connection:
            row = connection.execute('SELECT submission_id, endpoint_id, native_session_id, native_message_id, envelope_json, fingerprint FROM messages').fetchone()
        self.assertEqual(row[:4], (self.raw['submission_id'], self.binding.endpoint_id, 'native-session-1', self.raw['source']['message_id']))
        self.assertEqual(row[4], canonical_envelope(self.raw))
        self.assertEqual(row[5], envelope_fingerprint(self.raw))

    async def test_duplicate_returns_original_id_and_received_time(self):
        first = await self.accept()
        before = await self.store.get_message(first.message_id)
        duplicate = await self.accept(copy.deepcopy(self.raw))
        self.assertEqual(duplicate.status, c.AcceptStatus.ACCEPTED)
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(duplicate.message_id, first.message_id)
        self.assertEqual((await self.store.get_message(first.message_id)).received_at, before.received_at)
        self.assertEqual(self.count(), 1)

    async def test_key_order_does_not_change_existing_fingerprint(self):
        first = await self.accept()
        reordered = dict(reversed(tuple(self.raw.items())))
        reordered['source'] = dict(reversed(tuple(self.raw['source'].items())))
        result = await self.accept(reordered)
        self.assertTrue(result.duplicate)
        self.assertEqual(result.message_id, first.message_id)

    async def test_conflicting_body_does_not_overwrite(self):
        first = await self.accept()
        changed = copy.deepcopy(self.raw)
        changed['content']['text'] = 'Different body'
        result = await self.accept(changed)
        self.assertEqual(result.status, c.AcceptStatus.CONFLICT)
        self.assertEqual(result.error.code, 'SUBMISSION_CONFLICT')
        self.assertIsNone(result.message_id)
        self.assertEqual((await self.store.get_message(first.message_id)).envelope.to_dict(), self.raw)
        self.assertEqual(self.count(), 1)

    async def test_source_metadata_is_part_of_conflict_comparison(self):
        await self.accept()
        for field, value in (('message_id', 'other-native-message'), ('user_message_id', 'other-user'), ('turn_group_id', 'group-2')):
            changed = copy.deepcopy(self.raw)
            changed['source'][field] = value
            with self.subTest(field=field):
                self.assertEqual((await self.accept(changed)).status, c.AcceptStatus.CONFLICT)
        self.assertEqual(self.count(), 1)

    async def test_other_valid_endpoint_with_same_submission_conflicts(self):
        await self.accept()
        binding, _ = self.registry.register('chatgpt_web', 'session-2')
        changed = copy.deepcopy(self.raw)
        changed['source'].update(endpoint_id=binding.endpoint_id, native_session_id='session-2')
        self.assertEqual((await self.accept(changed)).status, c.AcceptStatus.CONFLICT)
        self.assertEqual(self.count(), 1)

    async def test_different_submission_ids_with_same_body_are_distinct(self):
        first = await self.accept()
        other = copy.deepcopy(self.raw)
        other['submission_id'] = 'sub_other_00000001'
        second = await self.accept(other)
        self.assertFalse(second.duplicate)
        self.assertNotEqual(first.message_id, second.message_id)
        self.assertEqual(self.count(), 2)

    async def test_input_mutation_does_not_change_saved_envelope(self):
        expected = copy.deepcopy(self.raw)
        result = await self.accept()
        self.raw['content']['text'] = 'Changed after acceptance'
        self.raw['source']['message_id'] = 'changed-native'
        self.assertEqual((await self.store.get_message(result.message_id)).envelope.to_dict(), expected)

    async def test_lookup_by_submission_and_missing_ids(self):
        result = await self.accept()
        message = await self.store.get_message_by_submission_id(c.SubmissionId(self.raw['submission_id']))
        self.assertEqual(message.message_id, result.message_id)
        self.assertIsNone(await self.store.get_message(c.MessageId('cfmsg_' + '0' * 32)))
        self.assertIsNone(await self.store.get_message_by_submission_id(c.SubmissionId('sub_absent_00000001')))

    async def test_close_reopen_restores_message_and_duplicate_identity(self):
        first = await self.accept()
        await self.store.close()
        self.store = SQLiteMessageStore(self.path, self.reader, busy_timeout=0.2)
        self.assertEqual((await self.store.get_message(first.message_id)).envelope.to_dict(), self.raw)
        duplicate = await self.accept()
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(duplicate.message_id, first.message_id)
        self.assertEqual(self.count(), 1)

    async def test_invalid_envelope_is_rejected_without_record(self):
        for raw in (None, {}, {'submission_id': self.raw['submission_id']}, {**self.raw, 'extra': True}):
            with self.subTest(raw_type=type(raw).__name__):
                result = await self.store.accept_message(c.SupervisorSubmission(envelope=raw))
                self.assertEqual(result.status, c.AcceptStatus.REJECTED)
                self.assertEqual(result.error.code, 'INVALID_ENVELOPE')
        self.assertEqual(self.count(), 0)

    async def test_unencodable_envelope_is_rejected_without_changing_v1_validator(self):
        raw = copy.deepcopy(self.raw)
        raw['content']['text'] = json.loads('"\\ud800"')
        c.SupervisorEnvelope.from_dict(raw)
        result = await self.accept(raw)
        self.assertEqual(result.status, c.AcceptStatus.REJECTED)
        self.assertEqual(result.error.code, 'INVALID_ENVELOPE_ENCODING')
        self.assertEqual(self.count(), 0)

    async def test_endpoint_not_found_is_rejected(self):
        self.raw['source']['endpoint_id'] = 'cfep_' + '0' * 32
        result = await self.accept()
        self.assertEqual(result.status, c.AcceptStatus.REJECTED)
        self.assertEqual(result.error.code, 'ENDPOINT_NOT_FOUND')
        self.assertEqual(self.count(), 0)

    async def test_native_session_mismatch_is_rejected(self):
        self.raw['source']['native_session_id'] = 'not-bound-session'
        result = await self.accept()
        self.assertEqual(result.error.code, 'SOURCE_SESSION_MISMATCH')
        self.assertEqual(self.count(), 0)

    async def test_provider_mismatch_is_rejected(self):
        binding, _ = self.registry.register('different-provider', 'native-session-1')
        self.raw['source']['endpoint_id'] = binding.endpoint_id
        result = await self.accept()
        self.assertEqual(result.error.code, 'SOURCE_PROVIDER_MISMATCH')
        self.assertEqual(self.count(), 0)

    async def test_registry_corruption_is_not_accepted(self):
        self.registry_path.write_text('{broken', encoding='utf-8')
        result = await self.accept()
        self.assertEqual(result.status, c.AcceptStatus.REJECTED)
        self.assertEqual(result.error.code, 'REGISTRY_CORRUPT')
        self.assertEqual(self.count(), 0)

    async def test_source_lookup_timeout_happens_before_database_write(self):
        class SlowReader:
            async def get_identity(self, endpoint_id):
                await asyncio.sleep(0.1)
        other = SQLiteMessageStore(self.path, SlowReader(), identity_timeout=0.01)
        try:
            result = await self.accept(store=other)
            self.assertEqual(result.error.code, 'SOURCE_LOOKUP_TIMEOUT')
            self.assertTrue(result.error.retryable)
            self.assertEqual(self.count(), 0)
        finally:
            await other.close()

    async def test_source_reader_cannot_substitute_other_endpoint_identity(self):
        class WrongReader:
            async def get_identity(self, endpoint_id):
                return c.EndpointIdentity(endpoint_id=c.EndpointId('cfep_' + 'f' * 32), provider='chatgpt_web', native_session_id=c.NativeSessionId('native-session-1'))
        other = SQLiteMessageStore(self.path, WrongReader())
        try:
            result = await self.accept(store=other)
            self.assertEqual(result.error.code, 'SOURCE_ENDPOINT_MISMATCH')
            self.assertEqual(self.count(), 0)
        finally:
            await other.close()

    async def test_insert_failure_rolls_back_and_retry_is_not_a_memory_duplicate(self):
        with database(self.path) as connection:
            connection.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON messages BEGIN SELECT RAISE(ABORT, 'forced insert failure'); END")
        result = await self.accept()
        self.assertEqual(result.status, c.AcceptStatus.REJECTED)
        self.assertEqual(result.error.code, 'MESSAGE_STORE_WRITE_FAILED')
        self.assertEqual(self.count(), 0)
        with database(self.path) as connection:
            connection.execute('DROP TRIGGER fail_insert')
        retry = await self.accept()
        self.assertEqual(retry.status, c.AcceptStatus.ACCEPTED)
        self.assertFalse(retry.duplicate)
        self.assertEqual(self.count(), 1)

    async def test_commit_failure_rolls_back_entire_transaction(self):
        with database(self.path) as connection:
            connection.executescript("""
                CREATE TABLE failure_parent (id TEXT PRIMARY KEY);
                CREATE TABLE failure_child (parent_id TEXT REFERENCES failure_parent(id) DEFERRABLE INITIALLY DEFERRED);
                CREATE TRIGGER fail_commit AFTER INSERT ON messages BEGIN
                    INSERT INTO failure_child VALUES ('missing');
                END;
            """)
        result = await self.accept()
        self.assertEqual(result.status, c.AcceptStatus.REJECTED)
        self.assertEqual(result.error.code, 'MESSAGE_STORE_WRITE_FAILED')
        self.assertEqual(self.count(), 0)
        with database(self.path) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM failure_child').fetchone()[0], 0)
            connection.execute('DROP TRIGGER fail_commit')
        retry = await self.accept()
        self.assertFalse(retry.duplicate)
        self.assertEqual(retry.status, c.AcceptStatus.ACCEPTED)

    async def test_lock_timeout_rejects_without_false_acceptance(self):
        blocker = sqlite3.connect(self.path, isolation_level=None)
        blocker.execute('BEGIN IMMEDIATE')
        try:
            result = await self.accept()
            self.assertEqual(result.status, c.AcceptStatus.REJECTED)
            self.assertEqual(result.error.code, 'MESSAGE_STORE_BUSY')
            self.assertTrue(result.error.retryable)
            self.assertEqual(self.count(), 0)
        finally:
            blocker.execute('ROLLBACK')
            blocker.close()
        self.assertFalse((await self.accept()).duplicate)
        self.assertEqual(self.count(), 1)

    async def test_closed_store_never_returns_accepted(self):
        await self.store.close()
        await self.store.close()
        result = await self.accept()
        self.assertEqual(result.status, c.AcceptStatus.REJECTED)
        self.assertEqual(result.error.code, 'MESSAGE_STORE_CLOSED')

    async def test_database_constraint_rejects_duplicate_without_python_lookup(self):
        await self.accept()
        with database(self.path) as connection:
            columns = [row[1] for row in connection.execute('PRAGMA table_info(messages)')]
            values = list(connection.execute('SELECT * FROM messages').fetchone())
            values[columns.index('message_id')] = 'cfmsg_' + '0' * 32
            placeholders = ','.join('?' for _ in values)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(f'INSERT INTO messages VALUES ({placeholders})', values)
        self.assertEqual(self.count(), 1)

    async def test_concurrent_connections_accept_exactly_once(self):
        other = SQLiteMessageStore(self.path, self.reader, busy_timeout=2.0)
        try:
            first, second = await asyncio.gather(self.accept(), self.accept(store=other))
            self.assertEqual(first.status, c.AcceptStatus.ACCEPTED)
            self.assertEqual(second.status, c.AcceptStatus.ACCEPTED)
            self.assertEqual(first.message_id, second.message_id)
            self.assertEqual(sorted((first.duplicate, second.duplicate)), [False, True])
            self.assertEqual(self.count(), 1)
        finally:
            await other.close()

    async def test_concurrent_conflicting_connections_preserve_winner(self):
        other = SQLiteMessageStore(self.path, self.reader, busy_timeout=2.0)
        changed = copy.deepcopy(self.raw)
        changed['content']['text'] = 'Competing body'
        try:
            results = await asyncio.gather(self.accept(), self.accept(changed, other))
            self.assertEqual(sorted(result.status.value for result in results), ['accepted', 'conflict'])
            winner = next(result for result in results if result.status is c.AcceptStatus.ACCEPTED)
            expected = self.raw if results[0] is winner else changed
            self.assertEqual((await self.store.get_message(winner.message_id)).envelope.to_dict(), expected)
            self.assertEqual(self.count(), 1)
        finally:
            await other.close()

    async def test_processing_marks_are_passive_idempotent_and_survive_reopen(self):
        result = await self.accept()
        error = c.ErrorInfo(code='WAITING', message='Pending supervisor decision', details={'reasons': ['one']})
        await self.store.mark_blocked(result.message_id, error)
        await self.store.mark_blocked(result.message_id, error)
        await self.store.close()
        self.store = SQLiteMessageStore(self.path, self.reader)
        message = await self.store.get_message(result.message_id)
        self.assertEqual(message.processing_state, c.ProcessingState.BLOCKED)
        self.assertEqual(message.processing_error.to_dict(), error.to_dict())
        self.assertEqual(message.envelope.to_dict(), self.raw)
        await self.store.mark_processed(result.message_id)
        await self.store.mark_processed(result.message_id)
        message = await self.store.get_message(result.message_id)
        self.assertEqual(message.processing_state, c.ProcessingState.PROCESSED)
        self.assertIsNone(message.processing_error)
        self.assertEqual(self.count(), 1)

    async def test_pending_query_filters_kind_state_and_limit(self):
        ids = []
        for index in range(3):
            raw = copy.deepcopy(self.raw)
            raw['submission_id'] = f'sub_pending_{index:08d}'
            ids.append((await self.accept(raw)).message_id)
        await self.store.mark_processed(ids[0])
        await self.store.mark_blocked(ids[2], c.ErrorInfo(code='BLOCKED', message='Reason'))
        pending = await self.store.list_pending(c.MessageKind.SUPERVISOR_INSTRUCTION, 1)
        self.assertEqual(tuple(message.message_id for message in pending), (ids[1],))
        self.assertEqual(await self.store.list_pending(c.MessageKind.AGENT_REPORT, 5), ())
        self.assertEqual(await self.store.list_pending(c.MessageKind.SUPERVISOR_INSTRUCTION, 0), ())

    async def test_missing_processing_target_and_invalid_query_arguments_raise(self):
        missing = c.MessageId('cfmsg_' + '0' * 32)
        with self.assertRaises(ContextFlowError):
            await self.store.mark_processed(missing)
        with self.assertRaises(ContextFlowError):
            await self.store.mark_blocked(missing, c.ErrorInfo(code='BLOCKED', message='Reason'))
        for call in (
            lambda: self.store.get_message(str(missing)),
            lambda: self.store.get_message_by_submission_id(self.raw['submission_id']),
            lambda: self.store.list_pending('supervisor_instruction', 1),
            lambda: self.store.list_pending(c.MessageKind.SUPERVISOR_INSTRUCTION, -1),
            lambda: self.store.list_pending(c.MessageKind.SUPERVISOR_INSTRUCTION, True),
        ):
            with self.assertRaises(ContextFlowError):
                await call()

    async def test_tampered_envelope_is_rejected_on_read_and_duplicate(self):
        result = await self.accept()
        tampered = copy.deepcopy(self.raw)
        tampered['content']['text'] = 'Tampered outside Store'
        with database(self.path) as connection:
            connection.execute('UPDATE messages SET envelope_json=? WHERE message_id=?', (canonical_envelope(tampered), str(result.message_id)))
        with self.assertRaises(ContextFlowError) as caught:
            await self.store.get_message(result.message_id)
        self.assertEqual(caught.exception.code, 'MESSAGE_STORE_CORRUPT')
        self.assertEqual((await self.accept()).error.code, 'MESSAGE_STORE_CORRUPT')
        self.assertEqual(self.count(), 1)

    async def test_tampered_redundant_source_is_rejected(self):
        result = await self.accept()
        with database(self.path) as connection:
            connection.execute("UPDATE messages SET native_message_id='wrong' WHERE message_id=?", (str(result.message_id),))
        with self.assertRaises(ContextFlowError) as caught:
            await self.store.get_message(result.message_id)
        self.assertEqual(caught.exception.code, 'MESSAGE_STORE_CORRUPT')

    async def test_internal_agent_submission_uses_frozen_store_contract_only(self):
        envelope = c.AgentReportEnvelope.from_dict(report_dict())
        submission = c.AgentSubmission(source_agent_id=c.AgentId('agent-1'), envelope=envelope)
        first = await self.store.accept_message(submission)
        second = await self.store.accept_message(submission)
        self.assertTrue(second.duplicate)
        self.assertEqual(first.message_id, second.message_id)
        stored = await self.store.get_message(first.message_id)
        self.assertEqual(stored.kind, c.MessageKind.AGENT_REPORT)
        self.assertEqual(stored.source_agent_id, c.AgentId('agent-1'))
        self.assertEqual(stored.envelope.to_dict(), envelope.to_dict())
        other = await self.store.accept_message(c.AgentSubmission(source_agent_id=c.AgentId('agent-2'), envelope=envelope))
        self.assertNotEqual(other.message_id, first.message_id)
        changed = c.AgentReportEnvelope.from_dict({**report_dict(), 'content': {'format': 'markdown', 'text': 'Different report'}})
        conflict = await self.store.accept_message(c.AgentSubmission(source_agent_id=c.AgentId('agent-1'), envelope=changed))
        self.assertEqual(conflict.status, c.AcceptStatus.CONFLICT)
        self.assertEqual(self.count(), 2)

    async def test_ignored_insert_does_not_return_accepted(self):
        with database(self.path) as connection:
            connection.execute('CREATE TRIGGER ignore_insert BEFORE INSERT ON messages BEGIN SELECT RAISE(IGNORE); END')
        result = await self.accept()
        self.assertEqual(result.status, c.AcceptStatus.REJECTED)
        self.assertEqual(result.error.code, 'MESSAGE_STORE_WRITE_FAILED')
        self.assertEqual(self.count(), 0)
        with database(self.path) as connection:
            connection.execute('DROP TRIGGER ignore_insert')
        retry = await self.accept()
        self.assertEqual(retry.status, c.AcceptStatus.ACCEPTED)
        self.assertFalse(retry.duplicate)

    async def test_ignored_processing_update_does_not_report_success(self):
        message_id = (await self.accept()).message_id
        with database(self.path) as connection:
            connection.execute('CREATE TRIGGER ignore_update BEFORE UPDATE ON messages BEGIN SELECT RAISE(IGNORE); END')
        with self.assertRaises(ContextFlowError) as caught:
            await self.store.mark_processed(message_id)
        self.assertEqual(caught.exception.code, 'MESSAGE_STORE_WRITE_FAILED')
        self.assertEqual((await self.store.get_message(message_id)).processing_state, c.ProcessingState.PENDING)

    async def test_after_insert_deletion_does_not_return_accepted(self):
        with database(self.path) as connection:
            connection.execute('CREATE TRIGGER erase_insert AFTER INSERT ON messages BEGIN DELETE FROM messages WHERE message_id=NEW.message_id; END')
        result = await self.accept()
        self.assertEqual(result.status, c.AcceptStatus.REJECTED)
        self.assertEqual(self.count(), 0)

    async def test_reopen_rejects_unknown_message_triggers(self):
        await self.store.close()
        with database(self.path) as connection:
            connection.execute('CREATE TRIGGER ignored BEFORE INSERT ON messages BEGIN SELECT RAISE(IGNORE); END')
        with self.assertRaises(ContextFlowError) as caught:
            SQLiteMessageStore(self.path, self.reader)
        self.assertEqual(caught.exception.code, 'MESSAGE_STORE_SCHEMA_ERROR')

    async def test_reopen_checks_partial_index_predicate(self):
        await self.store.close()
        with database(self.path) as connection:
            connection.execute('DROP INDEX messages_supervisor_submission_unique')
            connection.execute("CREATE UNIQUE INDEX messages_supervisor_submission_unique ON messages(submission_id) WHERE kind='agent_report'")
        with self.assertRaises(ContextFlowError) as caught:
            SQLiteMessageStore(self.path, self.reader)
        self.assertEqual(caught.exception.code, 'MESSAGE_STORE_SCHEMA_ERROR')

    async def test_reopen_checks_table_constraints_not_only_column_names(self):
        await self.store.close()
        with database(self.path) as connection:
            columns = [row[1] for row in connection.execute('PRAGMA table_info(messages)')]
            indexes = [row[0] for row in connection.execute("SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")]
            connection.execute('DROP TABLE messages')
            connection.execute('CREATE TABLE messages (' + ','.join(name + ' TEXT' for name in columns) + ')')
            for statement in indexes:
                connection.execute(statement)
        with self.assertRaises(ContextFlowError) as caught:
            SQLiteMessageStore(self.path, self.reader)
        self.assertEqual(caught.exception.code, 'MESSAGE_STORE_SCHEMA_ERROR')

    async def test_view_only_foreign_database_is_not_taken_over(self):
        path = Path(self.directory.name) / 'view-only.sqlite3'
        with database(path) as connection:
            connection.execute("CREATE VIEW original AS SELECT 'preserve' AS value")
        with self.assertRaises(ContextFlowError):
            SQLiteMessageStore(path, self.reader)
        with database(path) as connection:
            self.assertEqual(connection.execute('SELECT value FROM original').fetchone()[0], 'preserve')
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 0)
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='messages'").fetchone())

    async def test_foreign_versioned_schema_is_rejected_before_journal_change(self):
        path = Path(self.directory.name) / 'foreign-versioned.sqlite3'
        with database(path) as connection:
            connection.execute('CREATE TABLE original (value TEXT)')
            connection.execute('PRAGMA user_version=1')
            self.assertEqual(connection.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
        with self.assertRaises(ContextFlowError):
            SQLiteMessageStore(path, self.reader)
        with database(path) as connection:
            self.assertEqual(connection.execute('PRAGMA journal_mode').fetchone()[0], 'delete')

    async def test_failed_rollback_returns_rejection_and_disables_connection(self):
        with database(self.path) as connection:
            connection.execute("CREATE TRIGGER abort_insert BEFORE INSERT ON messages BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        def authorize(action, arg1, arg2, database, trigger):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and arg1 == 'ROLLBACK' else sqlite3.SQLITE_OK
        self.store._connection.set_authorizer(authorize)
        result = await self.accept()
        self.assertEqual(result.status, c.AcceptStatus.REJECTED)
        self.assertEqual(result.error.code, 'MESSAGE_STORE_WRITE_FAILED')
        self.assertEqual(result.error.details['rollback_error'], 'DatabaseError')
        self.assertEqual((await self.accept()).error.code, 'MESSAGE_STORE_CLOSED')
        self.assertEqual(self.count(), 0)

    async def test_failed_rollback_during_mark_is_converted_to_context_error(self):
        message_id = (await self.accept()).message_id
        with database(self.path) as connection:
            connection.execute("CREATE TRIGGER abort_update BEFORE UPDATE ON messages BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        def authorize(action, arg1, arg2, database, trigger):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and arg1 == 'ROLLBACK' else sqlite3.SQLITE_OK
        self.store._connection.set_authorizer(authorize)
        with self.assertRaises(ContextFlowError) as caught:
            await self.store.mark_processed(message_id)
        self.assertEqual(caught.exception.code, 'MESSAGE_STORE_WRITE_FAILED')
        self.assertEqual(caught.exception.details['rollback_error'], 'DatabaseError')
        with database(self.path) as connection:
            self.assertEqual(connection.execute('SELECT processing_state FROM messages').fetchone()[0], 'pending')

    async def test_unknown_schema_is_not_migrated_or_overwritten(self):
        await self.accept()
        await self.store.close()
        with database(self.path) as connection:
            connection.execute('PRAGMA user_version=99')
        with self.assertRaises(ContextFlowError) as caught:
            SQLiteMessageStore(self.path, self.reader)
        self.assertEqual(caught.exception.code, 'MESSAGE_STORE_SCHEMA_ERROR')
        self.assertEqual(self.count(), 1)
        with database(self.path) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 99)

    async def test_existing_unversioned_database_is_not_overwritten(self):
        other_path = Path(self.directory.name) / 'foreign.sqlite3'
        with database(other_path) as connection:
            connection.execute('CREATE TABLE original (value TEXT)')
            connection.execute("INSERT INTO original VALUES ('preserve')")
        with self.assertRaises(ContextFlowError):
            SQLiteMessageStore(other_path, self.reader)
        with database(other_path) as connection:
            self.assertEqual(connection.execute('SELECT value FROM original').fetchone()[0], 'preserve')


if __name__ == '__main__':
    unittest.main()
