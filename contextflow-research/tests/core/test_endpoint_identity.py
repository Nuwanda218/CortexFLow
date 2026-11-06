import tempfile
import unittest
from pathlib import Path

from contextflow_core.contracts import EndpointId, EndpointIdentityReader
from contextflow_core.endpoint_identity import RegistryEndpointIdentityReader
from contextflow_core.errors import ContextFlowError
from contextflow_core.registry import EndpointRegistry


class RegistryIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'endpoints.json'
        self.reader = RegistryEndpointIdentityReader(self.path)

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def test_implements_port_and_missing_registry_does_not_create_it(self):
        self.assertIsInstance(self.reader, EndpointIdentityReader)
        self.assertIsNone(await self.reader.get_identity(EndpointId('cfep_' + 'a' * 32)))
        self.assertFalse(self.path.exists())

    async def test_returns_persistent_identity_without_runtime_location(self):
        binding, _ = EndpointRegistry(self.path).register('chatgpt_web', 'session-1')
        identity = await self.reader.get_identity(EndpointId(binding.endpoint_id))
        self.assertEqual(str(identity.endpoint_id), binding.endpoint_id)
        self.assertEqual(identity.provider, 'chatgpt_web')
        self.assertEqual(str(identity.native_session_id), 'session-1')
        self.assertFalse(hasattr(identity, 'tab_id'))
        self.assertFalse(hasattr(identity, 'available'))

    async def test_reload_observes_bindings_added_after_reader_creation(self):
        registry = EndpointRegistry(self.path)
        first, _ = registry.register('chatgpt_web', 'session-1')
        self.assertIsNotNone(await self.reader.get_identity(EndpointId(first.endpoint_id)))
        second, _ = registry.register('chatgpt_web', 'session-2')
        self.assertIsNotNone(await self.reader.get_identity(EndpointId(second.endpoint_id)))

    async def test_unknown_endpoint_returns_none(self):
        EndpointRegistry(self.path).register('chatgpt_web', 'session-1')
        self.assertIsNone(await self.reader.get_identity(EndpointId('cfep_' + 'f' * 32)))

    async def test_corrupt_registry_is_not_silently_treated_as_empty(self):
        self.path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(ContextFlowError) as caught:
            await self.reader.get_identity(EndpointId('cfep_' + 'a' * 32))
        self.assertEqual(caught.exception.code, 'REGISTRY_CORRUPT')

    async def test_requires_endpoint_id_instead_of_plain_string(self):
        with self.assertRaises(ContextFlowError):
            await self.reader.get_identity('cfep_' + 'a' * 32)


if __name__ == '__main__':
    unittest.main()
