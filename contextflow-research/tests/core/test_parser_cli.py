import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from contextflow_core import contracts as c
from contextflow_core.endpoint_identity import RegistryEndpointIdentityReader
from contextflow_core.message_store import SQLiteMessageStore
from contextflow_core.parser_cli import main
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


class ParserCliTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.database = self.root / "messages.sqlite3"
        self.registry_path = self.root / "endpoints.json"
        self.directory_path = self.root / "agents.json"
        self.response_path = self.root / "response.json"
        registry = EndpointRegistry(self.registry_path)
        binding, _ = registry.register("chatgpt_web", "native-session-1")
        store = SQLiteMessageStore(
            self.database, RegistryEndpointIdentityReader(self.registry_path)
        )
        raw = supervisor_dict()
        raw["submission_id"] = "sub_cli_00000001"
        raw["source"].update(
            endpoint_id=binding.endpoint_id,
            message_id="native-cli-1",
        )
        raw["content"]["text"] = "请 Codex 修复 Adapter。"
        self.message_id = (
            await store.accept_message(c.SupervisorSubmission(envelope=raw))
        ).message_id
        await store.close()
        self.directory_path.write_text(
            json.dumps(DIRECTORY, ensure_ascii=False), encoding="utf-8"
        )
        self.response_path.write_text(
            json.dumps(
                {
                    "status": "parsed",
                    "instructions": [
                        {
                            "target_agent": "Codex",
                            "content": "修复 Adapter",
                            "source_excerpt": "Codex 修复 Adapter",
                        }
                    ],
                    "reason": None,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    async def asyncTearDown(self):
        self.directory.cleanup()

    def args(self, *extra):
        return [
            "--database", str(self.database),
            "--registry", str(self.registry_path),
            "--directory", str(self.directory_path),
            "--provider", "fake",
            "--fake-response", str(self.response_path),
            "--once",
            "--lease-ms", "2000",
            "--execution-timeout-ms", "500",
            "--submit-margin-ms", "100",
            "--retry-base-ms", "0",
            *extra,
        ]

    def openai_args(self, *extra):
        args = self.args()
        args[args.index("fake")] = "openai-compatible"
        fake_index = args.index("--fake-response")
        del args[fake_index:fake_index + 2]
        return [*args, *extra]

    async def test_openai_compatible_mode_uses_environment_config_and_closes_adapter(self):
        created = []

        class FakeProvider:
            def __init__(self, config):
                self.config = config
                self.closed = False
                created.append(self)

            async def extract(self, text, known_agents=None):
                return {
                    "status": "parsed",
                    "instructions": [{
                        "target_agent": "Codex",
                        "content": "修复 Adapter",
                        "source_excerpt": "Codex 修复 Adapter",
                    }],
                    "reason": None,
                }

            async def close(self):
                self.closed = True

        env = {
            "CONTEXTFLOW_OPENAI_API_KEY": "secret-value",
            "CONTEXTFLOW_OPENAI_BASE_URL": "https://ai.tju.edu.cn/api/v3",
            "CONTEXTFLOW_LLM_MODEL": "deepseek-v4.1-flash",
        }
        with patch.dict("os.environ", env, clear=False), patch(
            "contextflow_core.parser_cli.OpenAICompatibleExtractionAdapter",
            FakeProvider,
        ):
            result = await asyncio.to_thread(main, self.openai_args())

        self.assertEqual(result, 0)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].config.model, "deepseek-v4.1-flash")
        self.assertEqual(created[0].config.base_url, "https://ai.tju.edu.cn/api/v3")
        self.assertTrue(created[0].closed)

    def test_openai_compatible_mode_rejects_fake_response_argument(self):
        args = self.args()
        args[args.index("fake")] = "openai-compatible"
        self.assertEqual(main(args), 2)

    async def test_fake_cli_processes_one_message_and_repeat_is_idle(self):
        self.assertEqual(await asyncio.to_thread(main, self.args()), 0)
        self.assertEqual(await asyncio.to_thread(main, self.args()), 0)

        from contextflow_core.parse_jobs import SQLiteParseJobRepository
        repository = SQLiteParseJobRepository(self.database)
        try:
            formal = await repository.get_final_result(self.message_id)
            self.assertEqual(formal.result.status, c.ParseStatus.PARSED)
            self.assertEqual(formal.result.instructions[0].target_agent, "Codex")
        finally:
            await repository.close()

    async def test_partial_construction_failure_closes_already_opened_store(self):
        real_store = SQLiteMessageStore
        opened = []

        class TrackingStore(real_store):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                opened.append(self)

        with patch("contextflow_core.parser_cli.SQLiteMessageStore", TrackingStore), patch(
            "contextflow_core.parser_cli.SQLiteParseJobRepository",
            side_effect=RuntimeError("repository construction failed"),
        ):
            with self.assertRaises(RuntimeError):
                await __import__(
                    "contextflow_core.parser_cli", fromlist=["_run"]
                )._run(__import__(
                    "contextflow_core.parser_cli", fromlist=["_parser"]
                )._parser().parse_args(self.args()))

        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0]._closed)

    def test_cli_requires_every_explicit_path_and_fake_response(self):
        invalid = (
            [],
            ["--database", str(self.database)],
            [
                "--database", str(self.database),
                "--registry", str(self.registry_path),
                "--directory", str(self.directory_path),
                "--provider", "fake",
                "--once",
            ],
        )
        for args in invalid:
            with self.subTest(args=args):
                with self.assertRaises(SystemExit):
                    main(args)

    def test_fake_response_loader_rejects_duplicate_keys(self):
        self.response_path.write_text(
            '{"status":"parsed","status":"unresolved","instructions":[],"reason":"x"}',
            encoding="utf-8",
        )
        self.assertEqual(main(self.args()), 1)

    def test_anthropic_mode_rejects_fake_response_argument(self):
        args = self.args()
        index = args.index("fake")
        args[index] = "anthropic"
        self.assertEqual(main(args), 2)


if __name__ == "__main__":
    unittest.main()
