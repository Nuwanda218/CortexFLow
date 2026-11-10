import asyncio
import tempfile
import unittest
from pathlib import Path

from contextflow_core import contracts as c
from contextflow_core.endpoint_identity import RegistryEndpointIdentityReader
from contextflow_core.errors import ContextFlowError
from contextflow_core.fake_parser import BlockingStep, FakeInstructionParser
from contextflow_core.message_store import SQLiteMessageStore
from contextflow_core.parse_jobs import JobState, SQLiteParseJobRepository
from contextflow_core.parser_worker import (
    CycleStatus,
    ParserWorker,
    ParserWorkerConfig,
)
from contextflow_core.registry import EndpointRegistry
from tests.contracts.samples import supervisor_dict


def parsed(content="fix"):
    return c.ParseResult(
        status=c.ParseStatus.PARSED,
        instructions=(c.ParsedInstruction(target_agent="Codex", content=content),),
    )


def unresolved():
    return c.ParseResult(
        status=c.ParseStatus.UNRESOLVED,
        error=c.ErrorInfo(code="TARGET_AMBIGUOUS", message="target is ambiguous"),
    )


class ParserWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "messages.sqlite3"
        self.registry_path = Path(self.directory.name) / "endpoints.json"
        registry = EndpointRegistry(self.registry_path)
        self.binding, _ = registry.register("chatgpt_web", "native-session-1")
        self.store = SQLiteMessageStore(
            self.path, RegistryEndpointIdentityReader(self.registry_path), busy_timeout=1.0
        )
        self.repository = SQLiteParseJobRepository(self.path, busy_timeout=1.0)

    async def asyncTearDown(self):
        await self.repository.close()
        await self.store.close()
        self.directory.cleanup()

    async def accept(self, suffix="1", text="请 Codex 修复 Adapter"):
        raw = supervisor_dict()
        raw["submission_id"] = f"sub_worker_{suffix:0>8}"
        raw["source"].update(
            endpoint_id=self.binding.endpoint_id,
            message_id=f"native-worker-{suffix}",
        )
        raw["content"]["text"] = text
        accepted = await self.store.accept_message(c.SupervisorSubmission(envelope=raw))
        self.assertEqual(accepted.status, c.AcceptStatus.ACCEPTED)
        return accepted.message_id

    def config(self, **overrides):
        values = dict(
            lease_duration_ms=2000,
            execution_timeout_ms=500,
            submit_margin_ms=100,
            cancel_grace_ms=50,
            max_attempts=3,
            retry_base_ms=0,
            retry_cap_ms=100,
            discover_limit=10,
            poll_interval_ms=5,
        )
        values.update(overrides)
        return ParserWorkerConfig(**values)

    def worker(self, parser, *, repository=None, config=None, owner="worker-test"):
        return ParserWorker(
            repository=repository or self.repository,
            messages=self.store,
            parser=parser,
            config=config or self.config(),
            owner=owner,
        )

    async def test_fake_parser_supports_results_errors_and_records_calls(self):
        retryable = ContextFlowError("TEMP", "retry", retryable=True)
        parser = FakeInstructionParser((parsed("one"), unresolved(), retryable, "wrong"))

        self.assertEqual(await parser.parse("a", ("Codex",)), parsed("one"))
        self.assertEqual(await parser.parse("b", None), unresolved())
        with self.assertRaises(ContextFlowError):
            await parser.parse("c", ())
        self.assertEqual(await parser.parse("d", None), "wrong")
        self.assertEqual(
            parser.calls,
            [
                ("a", ("Codex",)),
                ("b", None),
                ("c", ()),
                ("d", None),
            ],
        )

    async def test_run_once_persists_parsed_and_restart_does_not_call_again(self):
        message_id = await self.accept()
        parser = FakeInstructionParser((parsed(),))
        first_worker = self.worker(parser)

        outcome = await first_worker.run_once()

        self.assertEqual(outcome.status, CycleStatus.FINALIZED)
        self.assertEqual(outcome.message_id, message_id)
        self.assertEqual(outcome.parse_status, c.ParseStatus.PARSED)
        self.assertEqual(len(parser.calls), 1)
        self.assertEqual(
            (await self.repository.get_final_result(message_id)).result, parsed()
        )
        self.assertEqual(
            (await self.store.get_message(message_id)).processing_state,
            c.ProcessingState.PENDING,
        )

        restarted = self.worker(parser, owner="worker-restarted")
        idle = await restarted.run_once()
        self.assertEqual(idle.status, CycleStatus.IDLE)
        self.assertEqual(len(parser.calls), 1)

    async def test_unresolved_is_formal_and_blocks_message(self):
        message_id = await self.accept("unresolved")
        parser = FakeInstructionParser((unresolved(),))

        outcome = await self.worker(parser).run_once()

        self.assertEqual(outcome.status, CycleStatus.FINALIZED)
        self.assertEqual(outcome.parse_status, c.ParseStatus.UNRESOLVED)
        message = await self.store.get_message(message_id)
        self.assertEqual(message.processing_state, c.ProcessingState.BLOCKED)
        self.assertEqual(message.processing_error.code, "TARGET_AMBIGUOUS")

    async def test_retryable_error_retries_with_new_generation_then_succeeds(self):
        message_id = await self.accept("retry")
        parser = FakeInstructionParser(
            (ContextFlowError("PROVIDER_BUSY", "retry", retryable=True), parsed("after"))
        )
        worker = self.worker(parser)

        first = await worker.run_once()
        second = await worker.run_once()

        self.assertEqual(first.status, CycleStatus.RETRY_SCHEDULED)
        self.assertEqual(second.status, CycleStatus.FINALIZED)
        job = await self.repository.get_job(message_id)
        self.assertEqual(job.attempt_count, 2)
        self.assertEqual(job.claim_generation, 2)
        self.assertEqual(len(parser.calls), 2)

    async def test_retryable_error_honors_persisted_provider_retry_after_hint(self):
        message_id = await self.accept("retry-after")
        retry_after_ms = 47_000
        parser = FakeInstructionParser((
            ContextFlowError(
                "LLM_RATE_LIMITED",
                "rate limited",
                retryable=True,
                details={"retry_after_ms": retry_after_ms},
            ),
        ))
        worker = self.worker(
            parser,
            config=self.config(retry_base_ms=0, retry_cap_ms=100),
        )
        before_ms = int(__import__("time").time() * 1000)

        outcome = await worker.run_once()

        self.assertEqual(outcome.status, CycleStatus.RETRY_SCHEDULED)
        job = await self.repository.get_job(message_id)
        self.assertEqual(job.state, JobState.RETRY_WAIT)
        self.assertGreaterEqual(job.next_attempt_at_ms, before_ms + retry_after_ms)
        self.assertEqual(job.last_error.details["retry_after_ms"], retry_after_ms)

    async def test_invalid_retry_after_hint_does_not_override_local_backoff(self):
        for index, value in enumerate((-1, True, "47000", 3_600_001)):
            with self.subTest(value=value):
                message_id = await self.accept(f"bad-retry-after-{index}")
                parser = FakeInstructionParser((
                    ContextFlowError(
                        "LLM_RATE_LIMITED",
                        "rate limited",
                        retryable=True,
                        details={"retry_after_ms": value},
                    ),
                ))
                worker = self.worker(
                    parser,
                    owner=f"worker-bad-hint-{index}",
                    config=self.config(retry_base_ms=10, retry_cap_ms=100),
                )
                before_ms = int(__import__("time").time() * 1000)
                await worker.run_once()
                job = await self.repository.get_job(message_id)
                self.assertIsNotNone(job.next_attempt_at_ms)
                self.assertLess(job.next_attempt_at_ms, before_ms + 1000)
                await self.store.mark_blocked(
                    message_id,
                    c.ErrorInfo(code="TEST_DONE", message="isolate the next subtest"),
                )

    async def test_retry_budget_exhausts_to_formal_failed_without_extra_call(self):
        message_id = await self.accept("exhaust")
        parser = FakeInstructionParser(
            (
                ContextFlowError("TEMP", "one", retryable=True),
                ContextFlowError("TEMP", "two", retryable=True),
            )
        )
        worker = self.worker(parser, config=self.config(max_attempts=2))

        self.assertEqual((await worker.run_once()).status, CycleStatus.RETRY_SCHEDULED)
        terminal = await worker.run_once()

        self.assertEqual(terminal.status, CycleStatus.FINALIZED)
        self.assertEqual(terminal.parse_status, c.ParseStatus.FAILED)
        self.assertEqual(len(parser.calls), 2)
        formal = await self.repository.get_final_result(message_id)
        self.assertEqual(formal.result.error.code, "PARSE_ATTEMPTS_EXHAUSTED")
        self.assertEqual(
            (await self.store.get_message(message_id)).processing_state,
            c.ProcessingState.BLOCKED,
        )

    async def test_expired_crash_after_last_attempt_is_finalized_without_another_call(self):
        message_id = await self.accept("crash-exhausted")
        await self.repository.discover_pending(limit=10, max_attempts=1)
        old = await self.repository.claim_next(
            "worker-crashed", "request-crashed", lease_duration_ms=100
        )
        self.assertEqual(
            (await self.repository.begin_attempt(old)).value, "authorized"
        )
        await asyncio.sleep(0.15)
        parser = FakeInstructionParser((parsed("must-not-run"),))
        recovered = self.worker(
            parser,
            config=self.config(max_attempts=99),
            owner="worker-recovery",
        )

        outcome = await recovered.run_once()

        self.assertEqual(outcome.status, CycleStatus.FINALIZED)
        self.assertEqual(outcome.parse_status, c.ParseStatus.FAILED)
        self.assertEqual(parser.calls, [])
        formal = await self.repository.get_final_result(message_id)
        self.assertEqual(formal.result.error.code, "PARSE_ATTEMPTS_EXHAUSTED")
        job = await self.repository.get_job(message_id)
        self.assertEqual((job.attempt_count, job.max_attempts), (1, 1))

    async def test_nonretryable_and_wrong_return_are_formal_failures(self):
        cases = (
            (
                ContextFlowError("BAD_REQUEST", "invalid", retryable=False),
                "BAD_REQUEST",
            ),
            (object(), "PARSER_CONTRACT_VIOLATION"),
            (RuntimeError("secret details"), "PARSER_EXECUTION_ERROR"),
        )
        for index, (step, expected_code) in enumerate(cases):
            with self.subTest(expected_code=expected_code):
                message_id = await self.accept(f"terminal-{index}")
                parser = FakeInstructionParser((step,))
                outcome = await self.worker(
                    parser, owner=f"worker-{index}"
                ).run_once()
                self.assertEqual(outcome.status, CycleStatus.FINALIZED)
                formal = await self.repository.get_final_result(message_id)
                self.assertEqual(formal.result.status, c.ParseStatus.FAILED)
                self.assertEqual(formal.result.error.code, expected_code)
                self.assertNotIn("secret details", formal.result.error.message)

    async def test_worker_does_not_call_parser_when_claim_has_insufficient_remaining_lease(self):
        await self.accept("lease-margin")
        parser = FakeInstructionParser((parsed("must-not-run"),))
        worker = self.worker(parser)
        original = self.repository.claim_next

        async def near_expiry(*args, **kwargs):
            claim = await original(*args, **kwargs)
            return type(claim)(
                message_id=claim.message_id,
                owner=claim.owner,
                claim_request_id=claim.claim_request_id,
                claim_generation=claim.claim_generation,
                lease_expires_at_ms=int(__import__("time").time() * 1000) + 20,
                attempt_count=claim.attempt_count,
                max_attempts=claim.max_attempts,
            )

        self.repository.claim_next = near_expiry
        outcome = await worker.run_once()

        self.assertEqual(outcome.status, CycleStatus.SKIPPED)
        self.assertEqual(parser.calls, [])

    async def test_parser_wait_does_not_hold_sqlite_write_transaction(self):
        first_id = await self.accept("wait")
        started = asyncio.Event()
        release = asyncio.Event()
        parser = FakeInstructionParser(
            (BlockingStep(parsed(), started=started, release=release),)
        )
        worker_task = asyncio.create_task(self.worker(parser).run_once())
        await asyncio.wait_for(started.wait(), 1.0)

        second_id = await asyncio.wait_for(self.accept("while-waiting"), 0.5)
        self.assertNotEqual(first_id, second_id)
        self.assertIsNone(await self.repository.get_job(second_id))

        release.set()
        self.assertEqual((await worker_task).status, CycleStatus.FINALIZED)

    async def test_timeout_cancels_cooperative_parser_and_schedules_retry(self):
        message_id = await self.accept("timeout")
        started = asyncio.Event()
        release = asyncio.Event()
        parser = FakeInstructionParser(
            (BlockingStep(parsed("late"), started=started, release=release),)
        )
        worker = self.worker(
            parser,
            config=self.config(
                lease_duration_ms=1000,
                execution_timeout_ms=20,
                submit_margin_ms=100,
                cancel_grace_ms=100,
            ),
        )

        outcome = await worker.run_once()

        self.assertEqual(outcome.status, CycleStatus.RETRY_SCHEDULED)
        job = await self.repository.get_job(message_id)
        self.assertEqual(job.state, JobState.RETRY_WAIT)
        self.assertEqual(job.last_error.code, "PARSER_TIMEOUT")
        self.assertIsNone(await self.repository.get_final_result(message_id))

    async def test_external_cancellation_requeues_cooperative_parser_and_propagates(self):
        message_id = await self.accept("cancel")
        started = asyncio.Event()
        release = asyncio.Event()
        parser = FakeInstructionParser(
            (BlockingStep(parsed(), started=started, release=release),)
        )
        worker = self.worker(parser)
        task = asyncio.create_task(worker.run_once())
        await asyncio.wait_for(started.wait(), 1.0)

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        job = await self.repository.get_job(message_id)
        self.assertEqual(job.state, JobState.RETRY_WAIT)
        self.assertEqual(job.last_error.code, "PARSER_CANCELLED")

    async def test_external_cancellation_does_not_release_uncooperative_claim(self):
        message_id = await self.accept("cancel-stalled")
        started = asyncio.Event()
        release = asyncio.Event()
        parser = FakeInstructionParser((
            BlockingStep(
                parsed("late"),
                started=started,
                release=release,
                ignore_cancellation=True,
            ),
        ))
        worker = self.worker(
            parser,
            config=self.config(cancel_grace_ms=10),
        )
        task = asyncio.create_task(worker.run_once())
        await asyncio.wait_for(started.wait(), 1.0)

        task.cancel()
        try:
            with self.assertRaises(asyncio.CancelledError):
                await task

            job = await self.repository.get_job(message_id)
            self.assertEqual(job.state, JobState.PROCESSING)
            self.assertIsNone(job.last_error)
            self.assertIsNone(await self.repository.get_final_result(message_id))
        finally:
            release.set()
            await asyncio.sleep(0)

    async def test_uncooperative_timeout_disables_worker_and_late_result_cannot_commit(self):
        message_id = await self.accept("stalled")
        started = asyncio.Event()
        release = asyncio.Event()
        parser = FakeInstructionParser(
            (
                BlockingStep(
                    parsed("late"),
                    started=started,
                    release=release,
                    ignore_cancellation=True,
                ),
            )
        )
        worker = self.worker(
            parser,
            config=self.config(
                lease_duration_ms=250,
                execution_timeout_ms=20,
                submit_margin_ms=50,
                cancel_grace_ms=10,
            ),
        )

        outcome = await worker.run_once()
        self.assertEqual(outcome.status, CycleStatus.STALLED)
        with self.assertRaises(ContextFlowError) as caught:
            await worker.run_once()
        self.assertEqual(caught.exception.code, "PARSER_WORKER_STALLED")
        self.assertIsNone(await self.repository.get_final_result(message_id))

        await asyncio.sleep(0.3)
        other_repo = SQLiteParseJobRepository(self.path)
        try:
            other_parser = FakeInstructionParser((parsed("new"),))
            other = self.worker(
                other_parser,
                repository=other_repo,
                config=self.config(),
                owner="worker-recovery",
            )
            recovered = await other.run_once()
            self.assertEqual(recovered.status, CycleStatus.FINALIZED)
            self.assertEqual(
                (await other_repo.get_final_result(message_id)).result, parsed("new")
            )
        finally:
            await other_repo.close()

        release.set()
        await asyncio.sleep(0)
        self.assertEqual(
            (await self.repository.get_final_result(message_id)).result,
            parsed("new"),
        )

    def test_config_rejects_timeout_without_submission_margin(self):
        invalid = (
            {"lease_duration_ms": 100, "execution_timeout_ms": 100},
            {"lease_duration_ms": 100, "execution_timeout_ms": 50, "submit_margin_ms": 50},
            {"max_attempts": 0},
            {"poll_interval_ms": True},
            {"retry_base_ms": 10, "retry_cap_ms": 5},
        )
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ContextFlowError) as caught:
                    self.config(**overrides)
                self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")


if __name__ == "__main__":
    unittest.main()
