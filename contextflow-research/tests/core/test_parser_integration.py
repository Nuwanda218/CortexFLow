import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from contextflow_core import contracts as c
from contextflow_core.agent_directory import ConfiguredAgentDirectory
from contextflow_core.endpoint_identity import RegistryEndpointIdentityReader
from contextflow_core.instruction_parser import LlmInstructionParser
from contextflow_core.message_store import SQLiteMessageStore
from contextflow_core.openai_provider import (
    OpenAICompatibleExtractionAdapter,
    OpenAIProviderConfig,
)
from contextflow_core.parse_jobs import SQLiteParseJobRepository
from contextflow_core.parser_worker import CycleStatus, ParserWorker, ParserWorkerConfig
from contextflow_core.registry import EndpointRegistry
from tests.contracts.samples import supervisor_dict


DIRECTORY = {
    "schema_version": 1,
    "agents": [
        {
            "agent_id": "claude-code-local",
            "name": "Claude Code",
            "aliases": ["Claude"],
            "available": True,
        },
        {
            "agent_id": "codex-local",
            "name": "Codex",
            "aliases": ["OpenAI Codex"],
            "available": True,
        },
    ],
}


class FakeProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def extract(self, text, known_agents=None):
        self.calls.append((text, known_agents))
        return self.responses.pop(0)


class FakeChatCompletions:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class ParserIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "messages.sqlite3"
        self.registry_path = Path(self.directory.name) / "endpoints.json"
        registry = EndpointRegistry(self.registry_path)
        self.binding, _ = registry.register("chatgpt_web", "native-session-1")
        self.store = SQLiteMessageStore(
            self.path, RegistryEndpointIdentityReader(self.registry_path)
        )
        self.repository = SQLiteParseJobRepository(self.path)

    async def asyncTearDown(self):
        await self.repository.close()
        await self.store.close()
        self.directory.cleanup()

    async def test_openai_adapter_parser_worker_and_sqlite_share_the_b1_lifecycle(self):
        text = "请 OpenAI Codex 修复 Adapter，再让 Claude Code 审查并运行测试。"
        raw = supervisor_dict()
        raw["submission_id"] = "sub_openai_integration_0001"
        raw["source"].update(
            endpoint_id=self.binding.endpoint_id,
            message_id="native-openai-integration",
        )
        raw["content"]["text"] = text
        accepted = await self.store.accept_message(c.SupervisorSubmission(envelope=raw))
        provider_raw = {
            "status": "parsed",
            "instructions": [
                {
                    "target_agent": "OpenAI Codex",
                    "content": "修复 Adapter",
                    "source_excerpt": "OpenAI Codex 修复 Adapter",
                },
                {
                    "target_agent": "Claude Code",
                    "content": "审查并运行测试",
                    "source_excerpt": "Claude Code 审查并运行测试",
                },
            ],
            "reason": None,
        }
        completions = FakeChatCompletions(SimpleNamespace(
            id="chatcmpl_integration",
            model="deepseek-v4.1-flash",
            choices=[SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=json.dumps(provider_raw, ensure_ascii=False)),
            )],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
        ))
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        adapter = OpenAICompatibleExtractionAdapter(
            OpenAIProviderConfig(
                api_key="secret-value",
                base_url="https://ai.tju.edu.cn/api/v3",
                model="deepseek-v4.1-flash",
                timeout_seconds=2,
                max_tokens=512,
            ),
            client=client,
        )
        directory = ConfiguredAgentDirectory.from_dict(DIRECTORY)
        parser = LlmInstructionParser(adapter, directory)
        worker = ParserWorker(
            repository=self.repository,
            messages=self.store,
            parser=parser,
            known_agents=parser.trusted_selectors(),
            owner="openai-worker",
            config=ParserWorkerConfig(
                lease_duration_ms=2000,
                execution_timeout_ms=500,
                submit_margin_ms=100,
                cancel_grace_ms=50,
                max_attempts=3,
                retry_base_ms=0,
                retry_cap_ms=100,
                discover_limit=10,
                poll_interval_ms=5,
            ),
        )

        outcome = await worker.run_once()

        self.assertEqual(outcome.status, CycleStatus.FINALIZED)
        formal = await self.repository.get_final_result(accepted.message_id)
        self.assertEqual(
            tuple((item.target_agent, item.content) for item in formal.result.instructions),
            (("Codex", "修复 Adapter"), ("Claude Code", "审查并运行测试")),
        )
        self.assertEqual(len(completions.calls), 1)
        self.assertEqual(completions.calls[0]["model"], "deepseek-v4.1-flash")
        self.assertEqual(
            (await self.store.get_message(accepted.message_id)).processing_state,
            c.ProcessingState.PENDING,
        )

    async def test_directory_provider_parser_worker_and_sqlite_form_one_chain(self):
        text = "请 OpenAI Codex 修复 Adapter，再让 Claude Code 审查并运行测试。"
        raw = supervisor_dict()
        raw["submission_id"] = "sub_b2_integration_00000001"
        raw["source"].update(
            endpoint_id=self.binding.endpoint_id,
            message_id="native-b2-integration",
        )
        raw["content"]["text"] = text
        accepted = await self.store.accept_message(c.SupervisorSubmission(envelope=raw))
        provider = FakeProvider((
            {
                "status": "parsed",
                "instructions": [
                    {
                        "target_agent": "OpenAI Codex",
                        "content": "修复 Adapter",
                        "source_excerpt": "OpenAI Codex 修复 Adapter",
                    },
                    {
                        "target_agent": "Claude Code",
                        "content": "审查并运行测试",
                        "source_excerpt": "Claude Code 审查并运行测试",
                    },
                ],
                "reason": None,
            },
        ))
        directory = ConfiguredAgentDirectory.from_dict(DIRECTORY)
        parser = LlmInstructionParser(provider, directory)
        worker = ParserWorker(
            repository=self.repository,
            messages=self.store,
            parser=parser,
            known_agents=parser.trusted_selectors(),
            owner="b2-worker",
            config=ParserWorkerConfig(
                lease_duration_ms=2000,
                execution_timeout_ms=500,
                submit_margin_ms=100,
                cancel_grace_ms=50,
                max_attempts=3,
                retry_base_ms=0,
                retry_cap_ms=100,
                discover_limit=10,
                poll_interval_ms=5,
            ),
        )

        outcome = await worker.run_once()

        self.assertEqual(outcome.status, CycleStatus.FINALIZED)
        formal = await self.repository.get_final_result(accepted.message_id)
        self.assertEqual(formal.message_id, accepted.message_id)
        self.assertEqual(
            tuple((item.target_agent, item.content) for item in formal.result.instructions),
            (("Codex", "修复 Adapter"), ("Claude Code", "审查并运行测试")),
        )
        self.assertIsNotNone(formal.result.intent_fingerprint)
        self.assertEqual(provider.calls, [(text, parser.trusted_selectors())])
        self.assertEqual(
            (await self.store.get_message(accepted.message_id)).processing_state,
            c.ProcessingState.PENDING,
        )

        restarted = ParserWorker(
            repository=self.repository,
            messages=self.store,
            parser=parser,
            known_agents=parser.trusted_selectors(),
            owner="b2-worker-restarted",
            config=worker.config,
        )
        self.assertEqual((await restarted.run_once()).status, CycleStatus.IDLE)
        self.assertEqual(len(provider.calls), 1)


if __name__ == "__main__":
    unittest.main()
