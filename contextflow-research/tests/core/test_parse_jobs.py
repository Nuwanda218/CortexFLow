import asyncio
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from contextflow_core import contracts as c
from contextflow_core.endpoint_identity import RegistryEndpointIdentityReader
from contextflow_core.errors import ContextFlowError
from contextflow_core.message_store import SQLiteMessageStore
from contextflow_core.parse_jobs import (
    AttemptDecision,
    JobState,
    ParseResultReader,
    SQLiteParseJobRepository,
)
from contextflow_core.registry import EndpointRegistry
from tests.contracts.samples import report_dict, supervisor_dict


class ParseJobRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "messages.sqlite3"
        self.registry_path = Path(self.directory.name) / "endpoints.json"
        self.registry = EndpointRegistry(self.registry_path)
        self.binding, _ = self.registry.register("chatgpt_web", "native-session-1")
        self.reader = RegistryEndpointIdentityReader(self.registry_path)
        self.store = SQLiteMessageStore(self.path, self.reader, busy_timeout=1.0)
        self.repositories: list[SQLiteParseJobRepository] = []
        self.repository = self.open_repository()

    async def asyncTearDown(self):
        for repository in self.repositories:
            await repository.close()
        await self.store.close()
        self.directory.cleanup()

    def open_repository(self, *, busy_timeout=1.0):
        repository = SQLiteParseJobRepository(self.path, busy_timeout=busy_timeout)
        self.repositories.append(repository)
        return repository

    async def accept_supervisor(self, suffix="0", text=None):
        raw = supervisor_dict()
        raw["submission_id"] = f"sub_parse_{suffix:0>8}"
        raw["source"].update(
            endpoint_id=self.binding.endpoint_id,
            message_id=f"native-message-{suffix}",
        )
        if text is not None:
            raw["content"]["text"] = text
        result = await self.store.accept_message(c.SupervisorSubmission(envelope=raw))
        self.assertEqual(result.status, c.AcceptStatus.ACCEPTED)
        return result.message_id

    async def test_discovery_creates_one_ready_job_only_for_pending_supervisor(self):
        first = await self.accept_supervisor("1")
        second = await self.accept_supervisor("2")
        await self.store.mark_blocked(
            second, c.ErrorInfo(code="BLOCKED", message="do not parse")
        )
        report = c.AgentReportEnvelope.from_dict(report_dict())
        agent = await self.store.accept_message(
            c.AgentSubmission(source_agent_id=c.AgentId("agent-1"), envelope=report)
        )
        self.assertEqual(agent.status, c.AcceptStatus.ACCEPTED)

        self.assertEqual(await self.repository.discover_pending(limit=10, max_attempts=3), 1)
        self.assertEqual(await self.repository.discover_pending(limit=10, max_attempts=9), 0)

        job = await self.repository.get_job(first)
        self.assertEqual(job.state, JobState.READY)
        self.assertEqual(job.claim_generation, 0)
        self.assertEqual(job.attempt_count, 0)
        self.assertEqual(job.max_attempts, 3)
        self.assertIsNone(await self.repository.get_job(second))
        self.assertIsNone(await self.repository.get_job(agent.message_id))

    async def test_discovery_rejects_trigger_that_rewrites_new_job_budget(self):
        message_id = await self.accept_supervisor("discover-tamper")
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("""
                CREATE TRIGGER alter_discovered_budget
                AFTER INSERT ON message_parse_jobs
                BEGIN UPDATE message_parse_jobs SET max_attempts=99
                    WHERE message_id=NEW.message_id; END
            """)
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(ContextFlowError) as caught:
            await self.repository.discover_pending(limit=10, max_attempts=3)

        self.assertEqual(caught.exception.code, "PARSE_JOB_WRITE_FAILED")
        self.assertIsNone(await self.repository.get_job(message_id))

    async def test_two_repositories_discover_same_message_once(self):
        message_id = await self.accept_supervisor("race")
        other = self.open_repository()

        counts = await asyncio.gather(
            self.repository.discover_pending(limit=10, max_attempts=3),
            other.discover_pending(limit=10, max_attempts=3),
        )

        self.assertEqual(sum(counts), 1)
        self.assertIsNotNone(await self.repository.get_job(message_id))

    async def test_claim_is_exclusive_idempotent_by_request_and_recoverable(self):
        message_id = await self.accept_supervisor("claim")
        await self.repository.discover_pending(limit=10, max_attempts=3)
        other = self.open_repository()

        first, second = await asyncio.gather(
            self.repository.claim_next("worker-a", "request-a", lease_duration_ms=5000),
            other.claim_next("worker-b", "request-b", lease_duration_ms=5000),
        )
        claims = [claim for claim in (first, second) if claim is not None]
        self.assertEqual(len(claims), 1)
        winner = claims[0]
        self.assertEqual(winner.message_id, message_id)
        self.assertEqual(winner.claim_generation, 1)

        duplicate = await (
            self.repository if winner.owner == "worker-a" else other
        ).claim_next(winner.owner, winner.claim_request_id, lease_duration_ms=5000)
        self.assertEqual(duplicate, winner)
        self.assertEqual(
            await other.find_claim(winner.owner, winner.claim_request_id), winner
        )
        loser = other if winner.owner == "worker-a" else self.repository
        self.assertIsNone(
            await loser.claim_next("worker-c", "request-c", lease_duration_ms=5000)
        )

    async def test_repeating_same_request_after_expiry_gets_new_generation(self):
        message_id = await self.accept_supervisor("same-request-expired")
        await self.repository.discover_pending(limit=10, max_attempts=3)
        first = await self.repository.claim_next(
            "worker-a", "request-a", lease_duration_ms=1
        )
        await asyncio.sleep(0.02)

        replacement = await self.repository.claim_next(
            "worker-a", "request-a", lease_duration_ms=5000
        )

        self.assertEqual(replacement.message_id, message_id)
        self.assertEqual(replacement.claim_generation, first.claim_generation + 1)
        self.assertGreater(replacement.lease_expires_at_ms, first.lease_expires_at_ms)

    async def test_claim_rejects_trigger_that_rewrites_the_owner(self):
        message_id = await self.accept_supervisor("claim-tamper")
        await self.repository.discover_pending(limit=10, max_attempts=3)
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("""
                CREATE TRIGGER alter_claim_owner
                AFTER UPDATE OF lease_owner ON message_parse_jobs
                WHEN NEW.lease_owner IS NOT NULL
                BEGIN UPDATE message_parse_jobs SET lease_owner='tampered-owner'
                    WHERE message_id=NEW.message_id; END
            """)
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(ContextFlowError) as caught:
            await self.repository.claim_next(
                "expected-owner", "expected-request", lease_duration_ms=5000
            )

        self.assertEqual(caught.exception.code, "PARSE_JOB_WRITE_FAILED")
        job = await self.repository.get_job(message_id)
        self.assertEqual(job.state, JobState.READY)
        self.assertEqual(job.claim_generation, 0)

    async def test_expired_claim_gets_new_generation_and_old_owner_is_fenced(self):
        message_id = await self.accept_supervisor("expired")
        await self.repository.discover_pending(limit=10, max_attempts=3)
        old = await self.repository.claim_next(
            "worker-old", "request-old", lease_duration_ms=1
        )
        await asyncio.sleep(0.02)
        other = self.open_repository()

        new = await other.claim_next(
            "worker-new", "request-new", lease_duration_ms=5000
        )

        self.assertEqual(new.message_id, message_id)
        self.assertEqual(new.claim_generation, old.claim_generation + 1)
        with self.assertRaises(ContextFlowError) as caught:
            await self.repository.begin_attempt(old)
        self.assertEqual(caught.exception.code, "PARSE_CLAIM_LOST")

    async def test_begin_attempt_rejects_trigger_that_increments_budget_twice(self):
        message_id = await self.accept_supervisor("attempt-tamper")
        await self.repository.discover_pending(limit=10, max_attempts=3)
        claim = await self.repository.claim_next(
            "worker-attempt", "request-attempt", lease_duration_ms=5000
        )
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("""
                CREATE TRIGGER alter_attempt_count
                AFTER UPDATE OF last_attempt_generation ON message_parse_jobs
                WHEN NEW.last_attempt_generation IS NOT NULL
                BEGIN UPDATE message_parse_jobs SET attempt_count=NEW.attempt_count+1
                    WHERE message_id=NEW.message_id; END
            """)
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(ContextFlowError) as caught:
            await self.repository.begin_attempt(claim)

        self.assertEqual(caught.exception.code, "PARSE_JOB_WRITE_FAILED")
        job = await self.repository.get_job(message_id)
        self.assertEqual(job.attempt_count, 0)
        self.assertIsNone(job.last_attempt_generation)

    async def test_begin_attempt_rejects_valid_but_wrong_generation_marker(self):
        message_id = await self.accept_supervisor("attempt-readback")
        await self.repository.discover_pending(limit=10, max_attempts=3)
        first = await self.repository.claim_next(
            "worker-first", "request-first", lease_duration_ms=5000
        )
        await self.repository.begin_attempt(first)
        await self.repository.schedule_retry(
            first,
            c.ErrorInfo(code="TEMP", message="retry", retryable=True),
            delay_ms=0,
        )
        second = await self.repository.claim_next(
            "worker-second", "request-second", lease_duration_ms=5000
        )
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("""
                CREATE TRIGGER rewrite_attempt_generation
                AFTER UPDATE OF last_attempt_generation ON message_parse_jobs
                WHEN NEW.last_attempt_generation=2
                BEGIN UPDATE message_parse_jobs SET last_attempt_generation=1
                    WHERE message_id=NEW.message_id; END
            """)
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(ContextFlowError) as caught:
            await self.repository.begin_attempt(second)

        self.assertEqual(caught.exception.code, "PARSE_JOB_WRITE_FAILED")
        job = await self.repository.get_job(message_id)
        self.assertEqual(job.attempt_count, 1)
        self.assertEqual(job.last_attempt_generation, 1)

    async def test_attempt_is_authorized_once_per_generation_and_budget_is_persistent(self):
        message_id = await self.accept_supervisor("budget")
        await self.repository.discover_pending(limit=10, max_attempts=1)
        claim = await self.repository.claim_next(
            "worker-a", "request-a", lease_duration_ms=5000
        )

        self.assertEqual(
            await self.repository.begin_attempt(claim), AttemptDecision.AUTHORIZED
        )
        self.assertEqual(
            await self.repository.begin_attempt(claim), AttemptDecision.ALREADY_RECORDED
        )
        job = await self.repository.get_job(message_id)
        self.assertEqual(job.attempt_count, 1)
        self.assertEqual(job.last_attempt_generation, claim.claim_generation)

        error = c.ErrorInfo(code="TEMPORARY", message="try later", retryable=True)
        with self.assertRaises(ContextFlowError) as caught:
            await self.repository.schedule_retry(claim, error, delay_ms=0)
        self.assertEqual(caught.exception.code, "PARSE_ATTEMPTS_EXHAUSTED")

        await self.repository.close()
        reopened = self.open_repository()
        restored = await reopened.get_job(message_id)
        self.assertEqual((restored.attempt_count, restored.max_attempts), (1, 1))

    async def test_retry_releases_claim_and_preserves_budget_across_new_generation(self):
        message_id = await self.accept_supervisor("retry")
        await self.repository.discover_pending(limit=10, max_attempts=2)
        first = await self.repository.claim_next(
            "worker-a", "request-a", lease_duration_ms=5000
        )
        self.assertEqual(await self.repository.begin_attempt(first), AttemptDecision.AUTHORIZED)
        error = c.ErrorInfo(code="TEMP", message="retry", retryable=True)

        job = await self.repository.schedule_retry(first, error, delay_ms=0)
        self.assertEqual(job.state, JobState.RETRY_WAIT)
        self.assertIsNone(job.lease_owner)
        self.assertEqual(job.last_error.to_dict(), error.to_dict())

        second = await self.repository.claim_next(
            "worker-b", "request-b", lease_duration_ms=5000
        )
        self.assertEqual(second.claim_generation, first.claim_generation + 1)
        self.assertEqual(await self.repository.begin_attempt(second), AttemptDecision.AUTHORIZED)
        restored = await self.repository.get_job(message_id)
        self.assertEqual(restored.attempt_count, 2)

    async def test_retry_rejects_trigger_that_rewrites_due_time(self):
        message_id = await self.accept_supervisor("retry-tamper")
        await self.repository.discover_pending(limit=10, max_attempts=3)
        claim = await self.repository.claim_next(
            "worker-retry", "request-retry", lease_duration_ms=5000
        )
        await self.repository.begin_attempt(claim)
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("""
                CREATE TRIGGER alter_retry_due
                AFTER UPDATE OF next_attempt_at_ms ON message_parse_jobs
                WHEN NEW.job_state='retry_wait'
                BEGIN UPDATE message_parse_jobs SET next_attempt_at_ms=NEW.next_attempt_at_ms+999999
                    WHERE message_id=NEW.message_id; END
            """)
            connection.commit()
        finally:
            connection.close()
        error = c.ErrorInfo(code="TEMP", message="retry", retryable=True)

        with self.assertRaises(ContextFlowError) as caught:
            await self.repository.schedule_retry(claim, error, delay_ms=10)

        self.assertEqual(caught.exception.code, "PARSE_JOB_WRITE_FAILED")
        job = await self.repository.get_job(message_id)
        self.assertEqual(job.state, JobState.PROCESSING)

    async def test_parsed_finalize_preserves_a_pending_message_diagnostic(self):
        message_id = await self.accept_supervisor("pending-diagnostic")
        diagnostic = c.ErrorInfo(code="EARLIER_NOTE", message="preserve this diagnostic")
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                "UPDATE messages SET processing_error_json=? WHERE message_id=?",
                (json.dumps(diagnostic.to_dict(), sort_keys=True, separators=(",", ":")), str(message_id)),
            )
            connection.commit()
        finally:
            connection.close()
        await self.repository.discover_pending(limit=10, max_attempts=3)
        claim = await self.repository.claim_next(
            "worker-diagnostic", "request-diagnostic", lease_duration_ms=5000
        )
        await self.repository.begin_attempt(claim)
        result = c.ParseResult(
            status=c.ParseStatus.PARSED,
            instructions=(c.ParsedInstruction(target_agent="Codex", content="work"),),
        )

        await self.repository.finalize(claim, result)

        message = await self.store.get_message(message_id)
        self.assertEqual(message.processing_state, c.ProcessingState.PENDING)
        self.assertEqual(message.processing_error.to_dict(), diagnostic.to_dict())

    async def test_parsed_finalize_is_atomic_and_message_remains_pending(self):
        message_id = await self.accept_supervisor("parsed")
        await self.repository.discover_pending(limit=10, max_attempts=3)
        claim = await self.repository.claim_next(
            "worker-a", "request-a", lease_duration_ms=5000
        )
        await self.repository.begin_attempt(claim)
        result = c.ParseResult(
            status=c.ParseStatus.PARSED,
            instructions=(
                c.ParsedInstruction(target_agent="Codex", content="fix"),
                c.ParsedInstruction(target_agent="Claude Code", content="test"),
            ),
        )

        stored = await self.repository.finalize(claim, result)

        self.assertEqual(stored.message_id, message_id)
        self.assertEqual(stored.result, result)
        self.assertGreater(stored.completed_at_ms, 0)
        self.assertIsInstance(self.repository, ParseResultReader)
        self.assertEqual(await self.repository.get_final_result(message_id), stored)
        message = await self.store.get_message(message_id)
        self.assertEqual(message.processing_state, c.ProcessingState.PENDING)
        self.assertIsNone(message.processing_error)

        duplicate = await self.repository.finalize(claim, result)
        self.assertEqual(duplicate, stored)
        changed = c.ParseResult(
            status=c.ParseStatus.PARSED,
            instructions=(c.ParsedInstruction(target_agent="Codex", content="different"),),
        )
        with self.assertRaises(ContextFlowError) as caught:
            await self.repository.finalize(claim, changed)
        self.assertEqual(caught.exception.code, "PARSE_RESULT_CONFLICT")

    async def test_unresolved_and_failed_finalize_atomically_block_message(self):
        for index, status in enumerate((c.ParseStatus.UNRESOLVED, c.ParseStatus.FAILED)):
            message_id = await self.accept_supervisor(f"blocked-{index}")
            await self.repository.discover_pending(limit=10, max_attempts=3)
            claim = await self.repository.claim_next(
                f"worker-{index}", f"request-{index}", lease_duration_ms=5000
            )
            await self.repository.begin_attempt(claim)
            error = c.ErrorInfo(
                code="TARGET_AMBIGUOUS" if status is c.ParseStatus.UNRESOLVED else "PARSE_FAILED",
                message="cannot safely parse",
            )
            result = c.ParseResult(status=status, error=error)

            await self.repository.finalize(claim, result)

            message = await self.store.get_message(message_id)
            self.assertEqual(message.processing_state, c.ProcessingState.BLOCKED)
            self.assertEqual(message.processing_error.to_dict(), error.to_dict())

    async def test_finalize_rejects_expired_claim_even_without_takeover(self):
        message_id = await self.accept_supervisor("late")
        await self.repository.discover_pending(limit=10, max_attempts=3)
        claim = await self.repository.claim_next(
            "worker-old", "request-old", lease_duration_ms=500
        )
        await self.repository.begin_attempt(claim)
        await asyncio.sleep(0.6)
        result = c.ParseResult(
            status=c.ParseStatus.PARSED,
            instructions=(c.ParsedInstruction(target_agent="Codex", content="late"),),
        )

        with self.assertRaises(ContextFlowError) as caught:
            await self.repository.finalize(claim, result)

        self.assertEqual(caught.exception.code, "PARSE_CLAIM_LOST")
        self.assertIsNone(await self.repository.get_final_result(message_id))
        self.assertEqual(
            (await self.store.get_message(message_id)).processing_state,
            c.ProcessingState.PENDING,
        )

    async def test_cancelled_await_preserves_cancellation_after_worker_failure(self):
        started = threading.Event()
        release = threading.Event()

        def failing_operation():
            started.set()
            release.wait(timeout=2)
            raise ContextFlowError("BACKGROUND_FAILURE", "background operation failed")

        task = asyncio.create_task(self.repository._run(failing_operation))
        self.assertTrue(await asyncio.to_thread(started.wait, 1))
        task.cancel()
        release.set()

        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_message_state_change_fences_owner_writes(self):
        message_id = await self.accept_supervisor("state-change")
        await self.repository.discover_pending(limit=10, max_attempts=3)
        claim = await self.repository.claim_next(
            "worker-a", "request-a", lease_duration_ms=5000
        )
        await self.store.mark_processed(message_id)

        with self.assertRaises(ContextFlowError) as caught:
            await self.repository.begin_attempt(claim)

        self.assertEqual(caught.exception.code, "PARSE_CLAIM_LOST")

    async def test_parsed_finalize_rejects_trigger_that_changes_message_state(self):
        message_id = await self.accept_supervisor("parsed-message-tamper")
        await self.repository.discover_pending(limit=10, max_attempts=3)
        claim = await self.repository.claim_next(
            "worker-parsed-tamper", "request-parsed-tamper", lease_duration_ms=5000
        )
        await self.repository.begin_attempt(claim)
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("""
                CREATE TRIGGER process_message_during_parse_finalize
                AFTER UPDATE OF result_status ON message_parse_jobs
                WHEN NEW.result_status='parsed'
                BEGIN UPDATE messages SET processing_state='processed', processing_error_json=NULL
                    WHERE message_id=NEW.message_id; END
            """)
            connection.commit()
        finally:
            connection.close()
        result = c.ParseResult(
            status=c.ParseStatus.PARSED,
            instructions=(c.ParsedInstruction(target_agent="Codex", content="work"),),
        )

        with self.assertRaises(ContextFlowError) as caught:
            await self.repository.finalize(claim, result)

        self.assertEqual(caught.exception.code, "PARSE_JOB_WRITE_FAILED")
        self.assertEqual((await self.repository.get_job(message_id)).state, JobState.PROCESSING)
        self.assertEqual(
            (await self.store.get_message(message_id)).processing_state,
            c.ProcessingState.PENDING,
        )

    async def test_finalize_rejects_valid_but_wrong_completion_timestamp(self):
        message_id = await self.accept_supervisor("finalize-readback")
        await self.repository.discover_pending(limit=10, max_attempts=3)
        claim = await self.repository.claim_next(
            "worker-finalize", "request-finalize", lease_duration_ms=5000
        )
        await self.repository.begin_attempt(claim)
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("""
                CREATE TRIGGER rewrite_completion_timestamp
                AFTER UPDATE OF completed_at_ms ON message_parse_jobs
                WHEN NEW.completed_at_ms IS NOT NULL
                BEGIN UPDATE message_parse_jobs SET completed_at_ms=NEW.completed_at_ms+1
                    WHERE message_id=NEW.message_id; END
            """)
            connection.commit()
        finally:
            connection.close()
        result = c.ParseResult(
            status=c.ParseStatus.PARSED,
            instructions=(c.ParsedInstruction(target_agent="Codex", content="work"),),
        )

        with self.assertRaises(ContextFlowError) as caught:
            await self.repository.finalize(claim, result)

        self.assertEqual(caught.exception.code, "PARSE_JOB_WRITE_FAILED")
        self.assertEqual((await self.repository.get_job(message_id)).state, JobState.PROCESSING)

    async def test_finalize_rejects_trigger_tampering_with_blocked_message(self):
        message_id = await self.accept_supervisor("tamper-message")
        await self.repository.discover_pending(limit=10, max_attempts=3)
        claim = await self.repository.claim_next(
            "worker-tamper", "request-tamper", lease_duration_ms=5000
        )
        await self.repository.begin_attempt(claim)
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("""
                CREATE TRIGGER alter_blocked_error
                AFTER UPDATE OF processing_state ON messages
                WHEN NEW.processing_state='blocked'
                BEGIN UPDATE messages SET processing_error_json=
                    '{"code":"TAMPERED","message":"wrong","retryable":false,"details":{}}'
                    WHERE message_id=NEW.message_id; END
            """)
            connection.commit()
        finally:
            connection.close()
        error = c.ErrorInfo(code="EXPECTED", message="expected failure")
        result = c.ParseResult(status=c.ParseStatus.FAILED, error=error)

        with self.assertRaises(ContextFlowError) as caught:
            await self.repository.finalize(claim, result)

        self.assertEqual(caught.exception.code, "PARSE_JOB_WRITE_FAILED")
        self.assertEqual((await self.repository.get_job(message_id)).state, JobState.PROCESSING)
        message = await self.store.get_message(message_id)
        self.assertEqual(message.processing_state, c.ProcessingState.PENDING)
        self.assertIsNone(message.processing_error)

    async def test_finalize_write_failure_rolls_back_job_and_message_together(self):
        message_id = await self.accept_supervisor("write-failure")
        await self.repository.discover_pending(limit=10, max_attempts=3)
        claim = await self.repository.claim_next(
            "worker-failure", "request-failure", lease_duration_ms=5000
        )
        await self.repository.begin_attempt(claim)
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("""
                CREATE TRIGGER abort_formal_result
                BEFORE UPDATE OF result_status ON message_parse_jobs
                BEGIN SELECT RAISE(ABORT, 'forced finalize failure'); END
            """)
            connection.commit()
        finally:
            connection.close()
        result = c.ParseResult(
            status=c.ParseStatus.FAILED,
            error=c.ErrorInfo(code="PARSE_FAILED", message="cannot parse"),
        )

        with self.assertRaises(ContextFlowError) as caught:
            await self.repository.finalize(claim, result)

        self.assertEqual(caught.exception.code, "PARSE_JOB_WRITE_FAILED")
        job = await self.repository.get_job(message_id)
        self.assertEqual(job.state, JobState.PROCESSING)
        self.assertIsNone(job.formal_result)
        message = await self.store.get_message(message_id)
        self.assertEqual(message.processing_state, c.ProcessingState.PENDING)
        self.assertIsNone(message.processing_error)

    async def test_finalize_commit_failure_rolls_back_job_and_message_together(self):
        message_id = await self.accept_supervisor("commit-failure")
        await self.repository.discover_pending(limit=10, max_attempts=3)
        claim = await self.repository.claim_next(
            "worker-commit", "request-commit", lease_duration_ms=5000
        )
        await self.repository.begin_attempt(claim)
        connection = sqlite3.connect(self.path)
        try:
            connection.executescript("""
                CREATE TABLE parse_failure_parent (id TEXT PRIMARY KEY);
                CREATE TABLE parse_failure_child (
                    parent_id TEXT REFERENCES parse_failure_parent(id)
                        DEFERRABLE INITIALLY DEFERRED
                );
                CREATE TRIGGER fail_parse_commit
                AFTER UPDATE OF result_status ON message_parse_jobs
                WHEN NEW.result_status IS NOT NULL
                BEGIN INSERT INTO parse_failure_child VALUES ('missing'); END;
            """)
        finally:
            connection.close()
        result = c.ParseResult(
            status=c.ParseStatus.FAILED,
            error=c.ErrorInfo(code="PARSE_FAILED", message="cannot parse"),
        )

        with self.assertRaises(ContextFlowError) as caught:
            await self.repository.finalize(claim, result)

        self.assertEqual(caught.exception.code, "PARSE_JOB_WRITE_FAILED")
        self.assertEqual((await self.repository.get_job(message_id)).state, JobState.PROCESSING)
        self.assertEqual(
            (await self.store.get_message(message_id)).processing_state,
            c.ProcessingState.PENDING,
        )

    async def test_invalid_repository_arguments_fail_before_sql(self):
        message_id = c.MessageId("cfmsg_" + "0" * 32)
        calls = (
            lambda: self.repository.discover_pending(limit=-1, max_attempts=3),
            lambda: self.repository.discover_pending(limit=1, max_attempts=0),
            lambda: self.repository.claim_next("", "request", lease_duration_ms=1),
            lambda: self.repository.claim_next("worker", "", lease_duration_ms=1),
            lambda: self.repository.claim_next("worker", "request", lease_duration_ms=True),
            lambda: self.repository.get_job(str(message_id)),
        )
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaises(ContextFlowError) as caught:
                    await call()
                self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    async def test_decoding_rejects_tampered_state_invariants_even_if_checks_bypassed(self):
        message_id = await self.accept_supervisor("tampered-state")
        await self.repository.discover_pending(limit=10, max_attempts=3)
        await self.repository.close()
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute(
                """
                UPDATE message_parse_jobs
                SET job_state='retry_wait', claim_generation=1,
                    next_attempt_at_ms=123, lease_owner='illegal-owner'
                WHERE message_id=?
                """,
                (str(message_id),),
            )
            connection.commit()
        finally:
            connection.close()
        reopened = self.open_repository()

        with self.assertRaises(ContextFlowError) as caught:
            await reopened.get_job(message_id)

        self.assertEqual(caught.exception.code, "PARSE_JOB_CORRUPT")

    async def test_decoding_rejects_tampered_cross_field_invariants_even_if_checks_bypassed(self):
        message_id = await self.accept_supervisor("tampered-row")
        await self.repository.discover_pending(limit=10, max_attempts=3)
        await self.repository.close()
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute(
                "UPDATE message_parse_jobs SET updated_at_ms='not-an-integer' WHERE message_id=?",
                (str(message_id),),
            )
            connection.commit()
        finally:
            connection.close()
        reopened = self.open_repository()

        with self.assertRaises(ContextFlowError) as caught:
            await reopened.get_job(message_id)

        self.assertEqual(caught.exception.code, "PARSE_JOB_CORRUPT")

    async def test_direct_sql_rejects_illegal_null_and_state_combinations(self):
        message_id = await self.accept_supervisor("constraints")
        await self.repository.discover_pending(limit=10, max_attempts=3)
        await self.repository.close()
        connection = sqlite3.connect(self.path)
        try:
            invalid_updates = (
                "UPDATE message_parse_jobs SET job_state='processing' WHERE message_id=?",
                "UPDATE message_parse_jobs SET max_attempts=0 WHERE message_id=?",
                "UPDATE message_parse_jobs SET attempt_count=1 WHERE message_id=?",
                "UPDATE message_parse_jobs SET claim_generation=1.5 WHERE message_id=?",
            )
            for statement in invalid_updates:
                with self.subTest(statement=statement):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(statement, (str(message_id),))
                    connection.rollback()
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
