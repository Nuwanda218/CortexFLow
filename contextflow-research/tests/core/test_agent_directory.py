import json
import tempfile
import unittest
from pathlib import Path

from contextflow_core import contracts as c
from contextflow_core.agent_directory import (
    AgentCandidate,
    ConfiguredAgentDirectory,
)
from contextflow_core.errors import ContextFlowError


VALID = {
    "schema_version": 1,
    "agents": [
        {
            "agent_id": "claude-code-local",
            "name": "Claude Code",
            "aliases": ["Claude", "CC"],
            "available": True,
        },
        {
            "agent_id": "codex-local",
            "name": "Codex",
            "aliases": ["OpenAI Codex"],
            "available": False,
        },
    ],
}


class AgentDirectoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_config_resolves_canonical_names_and_aliases(self):
        directory = ConfiguredAgentDirectory.from_dict(VALID)

        self.assertIsInstance(directory, c.AgentDirectory)
        self.assertEqual(await directory.known_agents(), ("Claude Code", "Codex"))
        self.assertEqual(
            await directory.resolve("Claude"),
            c.AgentDescriptor(agent_id=c.AgentId("claude-code-local"), available=True),
        )
        self.assertEqual(
            await directory.resolve("  openai CODEX  "),
            c.AgentDescriptor(agent_id=c.AgentId("codex-local"), available=False),
        )
        self.assertEqual(
            await directory.get_agent(c.AgentId("codex-local")),
            c.AgentDescriptor(agent_id=c.AgentId("codex-local"), available=False),
        )
        self.assertIsNone(await directory.resolve("unknown"))
        self.assertIsNone(await directory.get_agent(c.AgentId("missing-agent")))

    async def test_candidates_are_immutable_and_preserve_config_order(self):
        raw = json.loads(json.dumps(VALID))
        directory = ConfiguredAgentDirectory.from_dict(raw)
        raw["agents"][0]["name"] = "Changed"
        raw["agents"][0]["aliases"].append("Injected")

        candidates = directory.candidates()

        self.assertEqual(
            candidates,
            (
                AgentCandidate(
                    agent_id=c.AgentId("claude-code-local"),
                    name="Claude Code",
                    aliases=("Claude", "CC"),
                    available=True,
                ),
                AgentCandidate(
                    agent_id=c.AgentId("codex-local"),
                    name="Codex",
                    aliases=("OpenAI Codex",),
                    available=False,
                ),
            ),
        )
        with self.assertRaises(Exception):
            candidates[0].name = "mutated"

    async def test_empty_directory_is_valid_but_has_no_candidate(self):
        directory = ConfiguredAgentDirectory.from_dict(
            {"schema_version": 1, "agents": []}
        )

        self.assertEqual(await directory.known_agents(), ())
        self.assertEqual(directory.candidates(), ())
        self.assertIsNone(await directory.resolve("Codex"))

    async def test_selector_normalization_uses_nfkc_casefold_and_outer_trim(self):
        raw = json.loads(json.dumps(VALID))
        raw["agents"][0]["aliases"] = ["ＣＬＡＵＤＥ"]
        directory = ConfiguredAgentDirectory.from_dict(raw)

        self.assertEqual(
            (await directory.resolve("  claude ")).agent_id,
            c.AgentId("claude-code-local"),
        )

    async def test_rejects_duplicate_agent_ids_and_selector_collisions(self):
        cases = []
        duplicate_id = json.loads(json.dumps(VALID))
        duplicate_id["agents"][1]["agent_id"] = "claude-code-local"
        cases.append(duplicate_id)

        duplicate_alias = json.loads(json.dumps(VALID))
        duplicate_alias["agents"][1]["aliases"] = ["claude"]
        cases.append(duplicate_alias)

        canonical_alias_collision = json.loads(json.dumps(VALID))
        canonical_alias_collision["agents"][1]["name"] = "Ｃｌａｕｄｅ　Ｃｏｄｅ"
        cases.append(canonical_alias_collision)

        same_agent_duplicate = json.loads(json.dumps(VALID))
        same_agent_duplicate["agents"][0]["aliases"] = ["Claude", " claude "]
        cases.append(same_agent_duplicate)

        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(ContextFlowError) as caught:
                    ConfiguredAgentDirectory.from_dict(raw)
                self.assertEqual(caught.exception.code, "AGENT_DIRECTORY_INVALID")

    async def test_rejects_outer_whitespace_control_characters_and_ambiguous_ids(self):
        cases = []
        for field, value in (
            ("name", " Claude Code "),
            ("name", "Claude\nCode"),
            ("name", "Claude\x00Code"),
            ("agent_id", " agent-1 "),
            ("agent_id", "agent\n1"),
        ):
            raw = json.loads(json.dumps(VALID))
            raw["agents"][0][field] = value
            cases.append(raw)
        raw = json.loads(json.dumps(VALID))
        raw["agents"][0]["aliases"] = ["Alias\nInjected"]
        cases.append(raw)

        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(ContextFlowError) as caught:
                    ConfiguredAgentDirectory.from_dict(raw)
                self.assertEqual(caught.exception.code, "AGENT_DIRECTORY_INVALID")

    async def test_rejects_invalid_config_shape_and_values(self):
        cases = (
            None,
            {},
            {"schema_version": 2, "agents": []},
            {"schema_version": True, "agents": []},
            {"schema_version": 1, "agents": [], "extra": 1},
            {"schema_version": 1, "agents": {}},
            {"schema_version": 1, "agents": [None]},
            {
                "schema_version": 1,
                "agents": [{"agent_id": "a", "name": "A", "aliases": []}],
            },
            {
                "schema_version": 1,
                "agents": [
                    {
                        "agent_id": "",
                        "name": "A",
                        "aliases": [],
                        "available": True,
                    }
                ],
            },
            {
                "schema_version": 1,
                "agents": [
                    {
                        "agent_id": "a",
                        "name": " ",
                        "aliases": [],
                        "available": True,
                    }
                ],
            },
            {
                "schema_version": 1,
                "agents": [
                    {
                        "agent_id": "a",
                        "name": "A",
                        "aliases": "Alias",
                        "available": True,
                    }
                ],
            },
            {
                "schema_version": 1,
                "agents": [
                    {
                        "agent_id": "a",
                        "name": "A",
                        "aliases": [1],
                        "available": True,
                    }
                ],
            },
            {
                "schema_version": 1,
                "agents": [
                    {
                        "agent_id": "a",
                        "name": "A",
                        "aliases": [],
                        "available": 1,
                    }
                ],
            },
            {
                "schema_version": 1,
                "agents": [
                    {
                        "agent_id": "a",
                        "name": "A",
                        "aliases": [],
                        "available": True,
                        "role": "coder",
                    }
                ],
            },
        )
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(ContextFlowError) as caught:
                    ConfiguredAgentDirectory.from_dict(raw)
                self.assertEqual(caught.exception.code, "AGENT_DIRECTORY_INVALID")

    async def test_from_file_rejects_duplicate_keys_invalid_json_and_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agents.json"
            invalid_documents = (
                '{"schema_version":1,"schema_version":1,"agents":[]}',
                '{"schema_version":1,"agents":[{"agent_id":"a","name":"A","aliases":[],"available":true,"available":false}]}',
                '{broken',
                '{"schema_version":1,"agents":[],"value":NaN}',
            )
            for document in invalid_documents:
                with self.subTest(document=document):
                    path.write_text(document, encoding="utf-8")
                    with self.assertRaises(ContextFlowError) as caught:
                        ConfiguredAgentDirectory.from_file(path)
                    self.assertEqual(caught.exception.code, "AGENT_DIRECTORY_INVALID")

            with self.assertRaises(ContextFlowError) as caught:
                ConfiguredAgentDirectory.from_file(Path(directory) / "missing.json")
            self.assertEqual(caught.exception.code, "AGENT_DIRECTORY_OPEN_FAILED")

    async def test_runtime_methods_reject_wrong_identity_types(self):
        directory = ConfiguredAgentDirectory.from_dict(VALID)
        with self.assertRaises(ContextFlowError) as caught:
            await directory.resolve(c.AgentId("codex-local"))
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(ContextFlowError) as caught:
            await directory.get_agent("codex-local")
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")


if __name__ == "__main__":
    unittest.main()
