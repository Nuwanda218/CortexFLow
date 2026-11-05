import json
import tempfile
import unittest
from pathlib import Path

from contextflow_core.errors import ContextFlowError
from contextflow_core.registry import EndpointRegistry


class EndpointRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "endpoints.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_register_creates_core_managed_endpoint(self):
        registry = EndpointRegistry(self.path)

        binding, created = registry.register("chatgpt_web", "session-a", "chat")

        self.assertTrue(created)
        self.assertTrue(binding.endpoint_id.startswith("cfep_"))
        self.assertNotEqual(binding.endpoint_id, binding.native_session_id)
        self.assertEqual(registry.get(binding.endpoint_id), binding)

    def test_repeated_registration_is_idempotent(self):
        registry = EndpointRegistry(self.path)
        first, first_created = registry.register("chatgpt_web", "session-a", "chat")

        second, second_created = registry.register("chatgpt_web", "session-a", "project")

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(second, first)
        self.assertEqual(len(registry.all()), 1)

    def test_different_sessions_receive_different_endpoints(self):
        registry = EndpointRegistry(self.path)

        first, _ = registry.register("chatgpt_web", "session-a")
        second, _ = registry.register("chatgpt_web", "session-b")

        self.assertNotEqual(first.endpoint_id, second.endpoint_id)
        self.assertEqual(len(registry.all()), 2)

    def test_reload_preserves_endpoint_identity(self):
        first_registry = EndpointRegistry(self.path)
        first, _ = first_registry.register("chatgpt_web", "session-a", "chat")

        second_registry = EndpointRegistry(self.path)
        reloaded = second_registry.find("chatgpt_web", "session-a")

        self.assertEqual(reloaded, first)

    def test_persisted_document_has_version_and_no_tab_identity(self):
        registry = EndpointRegistry(self.path)
        registry.register("chatgpt_web", "session-a", "chat")

        data = json.loads(self.path.read_text(encoding="utf-8"))

        self.assertEqual(data["schema_version"], 1)
        self.assertNotIn("tab_id", json.dumps(data))

    def test_unknown_schema_is_not_silently_reset(self):
        self.path.write_text(
            json.dumps({"schema_version": 99, "endpoints": []}),
            encoding="utf-8",
        )

        with self.assertRaises(ContextFlowError) as caught:
            EndpointRegistry(self.path)

        self.assertEqual(caught.exception.code, "REGISTRY_CORRUPT")

    def test_duplicate_native_identity_is_rejected(self):
        record = {
            "provider": "chatgpt_web",
            "native_session_id": "session-a",
            "session_kind": "chat",
            "created_at": "2026-11-05T00:00:00Z",
            "updated_at": "2026-11-05T00:00:00Z",
        }
        data = {
            "schema_version": 1,
            "endpoints": [
                {"endpoint_id": "cfep_1", **record},
                {"endpoint_id": "cfep_2", **record},
            ],
        }
        self.path.write_text(json.dumps(data), encoding="utf-8")

        with self.assertRaises(ContextFlowError) as caught:
            EndpointRegistry(self.path)

        self.assertEqual(caught.exception.code, "REGISTRY_CORRUPT")


if __name__ == "__main__":
    unittest.main()
