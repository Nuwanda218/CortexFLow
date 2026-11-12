import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from contextflow_core import contracts as c
from contextflow_core.agent_directory import ConfiguredAgentDirectory
from contextflow_core.endpoint_identity import RegistryEndpointIdentityReader
from contextflow_core.fake_parser import FakeInstructionParser
from contextflow_core.message_store import SQLiteMessageStore
from contextflow_core.parse_jobs import SQLiteParseJobRepository
from contextflow_core.parse_result_codec import encode_parse_result
from contextflow_core.parser_worker import ParserWorker
from contextflow_core.registry import EndpointRegistry
from contextflow_core.task_store import SQLiteTaskRepository, TaskCreationService
from tests.contracts.samples import supervisor_dict


def parsed(*targets):
    return c.ParseResult(
        status=c.ParseStatus.PARSED,
        instructions=tuple(
            c.ParsedInstruction(target_agent=target, content=f"step {index + 1}")
            for index, target in enumerate(targets)
        ),
    )


class TaskPipelineCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = self.root / "messages.sqlite3"
        endpoints = self.root / "endpoints.json"
        self.endpoint, _ = EndpointRegistry(endpoints).register("chatgpt_web", "native-session-1")
        self.messages = SQLiteMessageStore(self.db, RegistryEndpointIdentityReader(endpoints))
        self.addAsyncCleanup(self.messages.close)
        self.jobs = SQLiteParseJobRepository(self.db)
        self.addAsyncCleanup(self.jobs.close)
        self.directory = ConfiguredAgentDirectory.from_dict({
            "schema_version": 1,
            "agents": [
                {"agent_id": "codex-local", "name": "Codex", "aliases": ["OpenAI Codex"], "available": True},
                {"agent_id": "claude-local", "name": "Claude", "aliases": [], "available": False},
            ],
        })

    async def submit_and_parse(self, result):
        raw = supervisor_dict()
        raw["source"]["endpoint_id"] = self.endpoint.endpoint_id
        accepted = await self.messages.accept_message(c.SupervisorSubmission(envelope=raw))
        worker = ParserWorker(
            repository=self.jobs, messages=self.messages, parser=FakeInstructionParser([result]),
        )
        await worker.run_once()
        return accepted.message_id

    def service(self):
        repository = SQLiteTaskRepository(self.db)
        return repository, TaskCreationService(repository, self.jobs, self.directory)


class TaskCreationTests(TaskPipelineCase):
    async def test_persisted_instructions_create_ordered_replayable_tasks(self):
        source = await self.submit_and_parse(parsed("OpenAI Codex", "Claude"))
        repository, service = self.service()
        batch = await service.create_tasks_from_parse_result(source)

        self.assertIs(batch.status, c.CreateTasksStatus.CREATED)
        self.assertEqual([task.target_agent_id for task in batch.tasks], ["codex-local", "claude-local"])
        self.assertEqual([task.instruction_index for task in batch.tasks], [0, 1])
        self.assertEqual([task.content for task in batch.tasks], ["step 1", "step 2"])
        self.assertTrue(all(task.state is c.TaskState.PENDING_DELIVERY for task in batch.tasks))
        self.assertTrue(all(delivery.state is c.DeliveryState.PENDING for delivery in batch.deliveries))
        self.assertTrue(all(task.parent_task_id is None for task in batch.tasks))
        self.assertEqual((await self.messages.get_message(source)).envelope.source.endpoint_id, self.endpoint.endpoint_id)

        reopened, replay_service = self.service()
        replay = await replay_service.create_tasks_from_parse_result(source)
        self.assertIs(replay.status, c.CreateTasksStatus.DUPLICATE)
        self.assertEqual(await reopened.get_tasks_for_message(source), batch.tasks)
        self.assertEqual(await repository.get_delivery(batch.deliveries[0].delivery_id), batch.deliveries[0])
        self.assertEqual(replay.deliveries, batch.deliveries)

        changed = parsed("Claude")
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute(
                "UPDATE message_parse_jobs SET instructions_json=?, intent_fingerprint=? WHERE message_id=?",
                (encode_parse_result(changed).instructions_json, changed.intent_fingerprint, source),
            )
        conflict = await replay_service.create_tasks_from_parse_result(source)
        self.assertIs(conflict.status, c.CreateTasksStatus.CONFLICT)
        self.assertEqual(conflict.error.code, "TASK_BATCH_CONFLICT")
        self.assertEqual(await reopened.get_tasks_for_message(source), batch.tasks)

    async def test_failed_parse_cannot_create_a_task(self):
        source = await self.submit_and_parse(c.ParseResult(
            status=c.ParseStatus.UNRESOLVED,
            error=c.ErrorInfo(code="PARSER_UNRESOLVED", message="no target"),
        ))
        repository, service = self.service()
        result = await service.create_tasks_from_parse_result(source)
        self.assertIs(result.status, c.CreateTasksStatus.REJECTED)
        self.assertEqual(await repository.get_tasks_for_message(source), ())

    async def test_write_failure_rolls_back_the_entire_batch(self):
        source = await self.submit_and_parse(parsed("Codex", "Claude"))
        repository, service = self.service()
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("""
                CREATE TRIGGER stop_second_task BEFORE INSERT ON tasks
                WHEN NEW.instruction_index = 1
                BEGIN SELECT RAISE(ABORT, 'injected write failure'); END
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            await service.create_tasks_from_parse_result(source)
        self.assertEqual(await repository.get_tasks_for_message(source), ())
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("DROP TRIGGER stop_second_task")
            self.assertEqual(connection.execute("SELECT count(*) FROM task_deliveries").fetchone()[0], 0)
        self.assertIs((await service.create_tasks_from_parse_result(source)).status, c.CreateTasksStatus.CREATED)
