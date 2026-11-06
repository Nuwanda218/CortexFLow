import asyncio
import json
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest.mock import patch

from websockets.exceptions import InvalidURI

from contextflow_core import contracts as c
from contextflow_core.endpoint_identity import RegistryEndpointIdentityReader
from contextflow_core.errors import ContextFlowError
from contextflow_core.message_store import SQLiteMessageStore
from contextflow_core.receiver import CoreReceiver, bridge_connection_settings
from contextflow_core.registry import EndpointRegistry
from tests.contracts.samples import report_dict, supervisor_dict


class MemorySocket:
    def __init__(self, frames, send_delay=0):
        self.frames = frames
        self.sent = []
        self.send_delay = send_delay

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for frame in self.frames:
            yield frame

    async def send(self, frame):
        if self.send_delay:
            await asyncio.sleep(self.send_delay)
        self.sent.append(json.loads(frame))


class ReceiverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.registry_path = root / 'endpoints.json'
        binding, _ = EndpointRegistry(self.registry_path).register('chatgpt_web', 'native-session-1')
        self.store = SQLiteMessageStore(root / 'messages.sqlite3', RegistryEndpointIdentityReader(self.registry_path))
        self.receiver = CoreReceiver(self.store)
        self.raw = supervisor_dict()
        self.raw['source']['endpoint_id'] = binding.endpoint_id

    async def asyncTearDown(self):
        await self.store.close()
        self.directory.cleanup()

    def frame(self, envelope=None):
        return {'type': 'submission', 'request_id': 'attempt-1', 'envelope': self.raw if envelope is None else envelope}

    async def test_receive_returns_original_contract_result(self):
        result = await self.receiver.receive(self.raw)
        self.assertEqual(result.status, c.AcceptStatus.ACCEPTED)
        self.assertIsNotNone(await self.store.get_message(result.message_id))

    async def test_wire_ack_preserves_existing_fields_and_keeps_core_id_internal(self):
        ack = await self.receiver.handle_frame(self.frame())
        self.assertEqual(set(ack), {'type', 'submission_id', 'status', 'duplicate', 'error', 'request_id'})
        self.assertEqual(ack['request_id'], 'attempt-1')
        self.assertEqual(ack['type'], 'submission_ack')
        self.assertEqual(ack['status'], 'accepted')
        self.assertFalse(ack['duplicate'])
        self.assertIsNone(ack['error'])
        message = await self.store.get_message_by_submission_id(c.SubmissionId(self.raw['submission_id']))
        self.assertIsNotNone(message)
        self.assertEqual(message.processing_state, c.ProcessingState.PENDING)
        self.assertNotIn('message_id', ack)

    async def test_classic_submission_without_request_id_keeps_legacy_ack_shape(self):
        ack = await self.receiver.handle_frame({'type': 'submission', 'envelope': self.raw})
        self.assertEqual(set(ack), {'type', 'submission_id', 'status', 'duplicate', 'error'})
        self.assertEqual(ack['status'], 'accepted')

    async def test_invalid_uri_exception_is_sanitized_without_secret_exception_chain(self):
        secret = 'PROBE_SECRET_NOT_REAL'
        invalid = InvalidURI('ws://invalid/ws?token=' + secret, 'invalid URI')
        with patch('contextflow_core.receiver.websockets.connect', side_effect=invalid):
            try:
                await self.receiver.run('ws://127.0.0.1:1/ws?token=test-token')
            except ContextFlowError as exc:
                rendered = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
                self.assertNotIn(secret, rendered)
            else:
                self.fail('InvalidURI was not converted to ContextFlowError')

    async def test_repeat_and_conflict_are_mapped_without_overwriting(self):
        await self.receiver.handle_frame(self.frame())
        duplicate = await self.receiver.handle_frame(self.frame())
        self.assertTrue(duplicate['duplicate'])
        changed = {**self.raw, 'content': {'format': 'markdown', 'text': 'Different'}}
        conflict = await self.receiver.handle_frame(self.frame(changed))
        self.assertEqual(conflict['status'], 'conflict')
        self.assertFalse(conflict['duplicate'])
        self.assertEqual(conflict['error']['code'], 'SUBMISSION_CONFLICT')

    async def test_invalid_envelope_keeps_available_submission_correlation(self):
        raw = {'submission_id': self.raw['submission_id']}
        ack = await self.receiver.handle_frame(self.frame(raw))
        self.assertEqual(ack['submission_id'], self.raw['submission_id'])
        self.assertEqual(ack['status'], 'rejected')
        self.assertEqual(ack['error']['code'], 'INVALID_ENVELOPE')

    async def test_invalid_nonobject_envelope_is_not_accepted(self):
        ack = await self.receiver.handle_frame({'type': 'submission', 'envelope': None})
        self.assertEqual(ack['status'], 'rejected')
        self.assertIsNone(ack['submission_id'])

    async def test_source_failure_and_closed_database_map_to_rejection(self):
        raw = {**self.raw, 'source': {**self.raw['source'], 'native_session_id': 'wrong'}}
        ack = await self.receiver.handle_frame(self.frame(raw))
        self.assertEqual(ack['error']['code'], 'SOURCE_SESSION_MISMATCH')
        await self.store.close()
        ack = await self.receiver.handle_frame(self.frame())
        self.assertEqual(ack['status'], 'rejected')
        self.assertEqual(ack['error']['code'], 'MESSAGE_STORE_CLOSED')

    async def test_no_agent_report_ingress_or_unrelated_message_handler(self):
        for frame in ({'type': 'welcome'}, {'type': 'agent_report', 'envelope': report_dict()}, [], None):
            self.assertIsNone(await self.receiver.handle_frame(frame))
        report_as_supervisor = await self.receiver.handle_frame(self.frame(report_dict()))
        self.assertEqual(report_as_supervisor['status'], 'rejected')
        self.assertEqual(await self.store.list_pending(c.MessageKind.AGENT_REPORT, 10), ())

    async def test_unexpected_store_error_does_not_leak_input_or_return_accepted(self):
        class BrokenStore:
            async def accept_message(self, submission):
                raise RuntimeError('sensitive-internal-value')
        ack = await CoreReceiver(BrokenStore()).handle_frame(self.frame())
        self.assertEqual(ack['status'], 'rejected')
        self.assertEqual(ack['error']['code'], 'CORE_RECEIVER_ERROR')
        self.assertNotIn('sensitive-internal-value', json.dumps(ack))

    async def test_connection_handler_ignores_bad_frames_and_acknowledges_valid_submission(self):
        ready = asyncio.Event()
        socket = MemorySocket(['{broken', '[]', json.dumps({'type': 'welcome'}), json.dumps(self.frame())])
        await self.receiver.serve_connection(socket, ready=ready)
        self.assertTrue(ready.is_set())
        self.assertEqual(len(socket.sent), 1)
        self.assertEqual(socket.sent[0]['status'], 'accepted')

    async def test_once_stops_after_accepted_not_after_rejected(self):
        bad = self.frame({'submission_id': 'sub_bad_00000001'})
        socket = MemorySocket([json.dumps(bad), json.dumps(self.frame()), json.dumps(self.frame())])
        await self.receiver.serve_connection(socket, once=True)
        self.assertEqual([ack['status'] for ack in socket.sent], ['rejected', 'accepted'])

    async def test_ack_timeout_does_not_undo_committed_message(self):
        receiver = CoreReceiver(self.store, ack_timeout=0.01)
        socket = MemorySocket([json.dumps(self.frame())], send_delay=0.1)
        with self.assertRaises(ContextFlowError) as caught:
            await receiver.serve_connection(socket)
        self.assertEqual(caught.exception.code, 'ACK_SEND_TIMEOUT')
        retry = await self.receiver.handle_frame(self.frame())
        self.assertEqual(retry['status'], 'accepted')
        self.assertTrue(retry['duplicate'])

    async def test_cancelled_source_lookup_does_not_emit_ack(self):
        started = asyncio.Event()
        class WaitingStore:
            async def accept_message(self, submission):
                started.set()
                await asyncio.Event().wait()
        socket = MemorySocket([json.dumps(self.frame())])
        task = asyncio.create_task(CoreReceiver(WaitingStore()).serve_connection(socket))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(socket.sent, [])


class ReceiverConfigurationTests(unittest.TestCase):
    def test_uri_and_token_are_configuration_not_hardcoded_defaults(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaises(ContextFlowError):
                bridge_connection_settings()
        with patch.dict('os.environ', {'CONTEXTFLOW_BRIDGE_URI': 'ws://127.0.0.1:9000/ws', 'CF_BRIDGE_TOKEN': 'test-only-token'}, clear=True):
            uri, origin = bridge_connection_settings()
            self.assertEqual(uri, 'ws://127.0.0.1:9000/ws?token=test-only-token')
            self.assertEqual(origin, 'http://127.0.0.1:9000')

    def test_explicit_uri_token_is_preserved_and_origin_can_be_supplied(self):
        uri, origin = bridge_connection_settings('wss://localhost/ws?token=test-token', 'https://localhost')
        self.assertEqual(uri, 'wss://localhost/ws?token=test-token')
        self.assertEqual(origin, 'https://localhost')

    def test_bad_uri_or_missing_token_are_rejected_without_echoing_uri(self):
        with patch.dict('os.environ', {}, clear=True):
            for value in ('http://localhost/ws?token=sensitive', 'ws://localhost/ws', 'ws://localhost/ws?token=a&token=b', 'ws://user:password@localhost/ws?token=sensitive', 'ws://@127.0.0.1:1/ws?token=sensitive', 'ws://:@localhost/ws?token=sensitive'):
                with self.subTest(case=value.split('?')[0]):
                    with self.assertRaises(ContextFlowError) as caught:
                        bridge_connection_settings(value)
                    self.assertNotIn('sensitive', caught.exception.message)
                    self.assertNotIn('password', caught.exception.message)


if __name__ == '__main__':
    unittest.main()
