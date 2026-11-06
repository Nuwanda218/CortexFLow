import asyncio
import copy
import json
import os
import sqlite3
import subprocess
import sys
import urllib.request

import websockets
from websockets.exceptions import ConnectionClosed

from contextflow_core import contracts as c
from contextflow_core.endpoint_identity import RegistryEndpointIdentityReader
from contextflow_core.errors import ContextFlowError
from contextflow_core.message_store import SQLiteMessageStore
from contextflow_core.receiver import CoreReceiver
from tests.adapter.test_bridge_protocol import BridgeTestCase, ROOT, TOKEN, make_envelope, recv_until


class BridgeOwnershipTests(BridgeTestCase):
    def uri(self, page=False):
        return f"ws://127.0.0.1:{self.port}/{'ws_page' if page else 'ws'}?token={TOKEN}"

    async def wait_external_release(self):
        for _ in range(100):
            with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/health', timeout=1) as response:
                health = json.load(response)
            if health['external_clients'] == 0:
                return
            await asyncio.sleep(0.02)
        self.fail('external connection was not released')

    async def test_second_external_is_rejected_without_taking_over(self):
        extra = await websockets.connect(self.uri(), origin=self.origin)
        try:
            with self.assertRaises(ConnectionClosed) as caught:
                await extra.recv()
            self.assertEqual(caught.exception.rcvd.code, 4409)
            await self.page.send(json.dumps({'type': 'adapter_submission', 'request_id': 'owned', 'envelope': make_envelope('sub_owned_00000001', 'session-abc', self.binding.endpoint_id)}))
            delivered = await recv_until(self.core, lambda message: message.get('type') == 'submission')
            self.assertEqual(delivered['request_id'], 'owned')
        finally:
            await extra.close()

    async def test_concurrent_external_connections_have_exactly_one_owner(self):
        await self.core.close()
        await self.wait_external_release()
        sockets = await asyncio.gather(websockets.connect(self.uri(), origin=self.origin), websockets.connect(self.uri(), origin=self.origin))
        outcomes = []
        try:
            for socket in sockets:
                try:
                    frame = json.loads(await asyncio.wait_for(socket.recv(), 2))
                    outcomes.append(frame['type'])
                except ConnectionClosed as exc:
                    outcomes.append(exc.rcvd.code)
            self.assertEqual(outcomes.count('welcome'), 1)
            self.assertEqual(outcomes.count(4409), 1)
        finally:
            for socket in sockets:
                await socket.close()

    async def test_late_ack_from_replacement_core_cannot_confirm_old_delivery(self):
        envelope = make_envelope('sub_late_00000001', 'session-abc', self.binding.endpoint_id)
        await self.page.send(json.dumps({'type': 'adapter_submission', 'request_id': 'old-attempt', 'envelope': envelope}))
        await recv_until(self.core, lambda message: message.get('type') == 'submission')
        await self.core.close()
        await self.wait_external_release()
        self.core = await websockets.connect(self.uri(), origin=self.origin)
        await recv_until(self.core, lambda message: message.get('type') == 'welcome')
        ack = {'type': 'submission_ack', 'submission_id': envelope['submission_id'], 'status': 'accepted'}
        await self.core.send(json.dumps(ack))
        with self.assertRaises(asyncio.TimeoutError):
            await recv_until(self.page, lambda message: message.get('type') == 'submission_ack', 0.3)
        await self.page.send(json.dumps({'type': 'adapter_submission', 'request_id': 'new-attempt', 'envelope': envelope}))
        await recv_until(self.core, lambda message: message.get('type') == 'submission')
        await self.core.send(json.dumps(ack))
        result = await recv_until(self.page, lambda message: message.get('type') == 'submission_ack')
        self.assertEqual(result['request_id'], 'new-attempt')

    async def test_late_ack_is_not_routed_to_replacement_page(self):
        envelope = make_envelope('sub_page_00000001', 'session-abc', self.binding.endpoint_id)
        await self.page.send(json.dumps({'type': 'adapter_submission', 'request_id': 'old-page', 'envelope': envelope}))
        await recv_until(self.core, lambda message: message.get('type') == 'submission')
        await self.page.close()
        self.page = await websockets.connect(self.uri(page=True), origin=self.origin)
        await recv_until(self.page, lambda message: message.get('type') == 'welcome')
        await self.core.send(json.dumps({'type': 'submission_ack', 'submission_id': envelope['submission_id'], 'status': 'accepted'}))
        with self.assertRaises(asyncio.TimeoutError):
            await recv_until(self.page, lambda message: message.get('type') == 'submission_ack', 0.3)

    async def test_changed_inflight_envelope_cannot_receive_previous_content_ack(self):
        envelope = make_envelope('sub_inflight_00000001', 'session-abc', self.binding.endpoint_id)
        await self.page.send(json.dumps({'type': 'adapter_submission', 'request_id': 'first', 'envelope': envelope}))
        await recv_until(self.core, lambda message: message.get('type') == 'submission')
        changed = copy.deepcopy(envelope)
        changed['content']['text'] = 'Changed before first ack'
        await self.page.send(json.dumps({'type': 'adapter_submission', 'request_id': 'changed', 'envelope': changed}))
        result = await recv_until(self.page, lambda message: message.get('type') == 'submission_ack')
        self.assertEqual(result['request_id'], 'changed')
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(result['error']['code'], 'SUBMISSION_INFLIGHT_CONFLICT')
        await self.core.send(json.dumps({'type': 'submission_ack', 'submission_id': envelope['submission_id'], 'status': 'accepted'}))
        result = await recv_until(self.page, lambda message: message.get('type') == 'submission_ack')
        self.assertEqual(result['request_id'], 'first')


class CoreReceiverBridgeTests(BridgeTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.database_path = self.registry_path.with_name('messages.sqlite3')
        self.store = SQLiteMessageStore(self.database_path, RegistryEndpointIdentityReader(self.registry_path), busy_timeout=0.05)
        self.receiver = CoreReceiver(self.store)
        self.receiver_task = None
        self.envelope = make_envelope('sub_receiver_00000001', 'session-abc', self.binding.endpoint_id)

    async def asyncTearDown(self):
        await self.stop_receiver()
        await self.store.close()
        await super().asyncTearDown()

    async def stop_receiver(self):
        if self.receiver_task is not None:
            self.receiver_task.cancel()
            try:
                await self.receiver_task
            except asyncio.CancelledError:
                pass
            self.receiver_task = None

    async def start_receiver(self):
        await self.core.close()
        for _ in range(100):
            with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/health', timeout=1) as response:
                if json.load(response)['external_clients'] == 0:
                    break
            await asyncio.sleep(0.02)
        ready = asyncio.Event()
        self.receiver_task = asyncio.create_task(self.receiver.run(f'ws://127.0.0.1:{self.port}/ws?token={TOKEN}', self.origin, ready=ready))
        try:
            await asyncio.wait_for(ready.wait(), 3)
        except asyncio.TimeoutError:
            if self.receiver_task.done():
                self.receiver_task.result()
            raise

    async def submit(self, envelope=None, request_id='attempt'):
        await self.page.send(json.dumps({'type': 'adapter_submission', 'request_id': request_id, 'envelope': self.envelope if envelope is None else envelope}))
        return await recv_until(self.page, lambda message: message.get('type') == 'submission_ack' and message.get('request_id') == request_id)

    def count(self):
        connection = sqlite3.connect(self.database_path)
        try:
            return connection.execute('SELECT count(*) FROM messages').fetchone()[0]
        finally:
            connection.close()

    async def test_real_bridge_to_sqlite_accept_duplicate_conflict_and_distinct_id(self):
        await self.start_receiver()
        first = await self.submit(request_id='first')
        self.assertEqual(first['status'], 'accepted')
        self.assertFalse(first['duplicate'])
        self.assertEqual(self.count(), 1)
        duplicate = await self.submit(request_id='duplicate')
        self.assertTrue(duplicate['duplicate'])
        changed = copy.deepcopy(self.envelope)
        changed['content']['text'] = 'Changed body'
        conflict = await self.submit(changed, 'conflict')
        self.assertEqual(conflict['status'], 'conflict')
        stored = await self.store.get_message_by_submission_id(c.SubmissionId(self.envelope['submission_id']))
        self.assertEqual(stored.envelope.to_dict(), self.envelope)
        other = copy.deepcopy(self.envelope)
        other['submission_id'] = 'sub_other_receiver_00000001'
        self.assertEqual((await self.submit(other, 'other'))['status'], 'accepted')
        self.assertEqual(self.count(), 2)

    async def test_failed_sqlite_write_returns_rejected_ack_and_retry_creates_new_record(self):
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("CREATE TRIGGER reject_message BEFORE INSERT ON messages BEGIN SELECT RAISE(ABORT, 'test failure'); END")
            connection.commit()
        finally:
            connection.close()
        await self.start_receiver()
        failed = await self.submit(request_id='write-failed')
        self.assertEqual(failed['status'], 'rejected')
        self.assertEqual(failed['error']['code'], 'MESSAGE_STORE_WRITE_FAILED')
        self.assertEqual(self.count(), 0)
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute('DROP TRIGGER reject_message')
            connection.commit()
        finally:
            connection.close()
        retry = await self.submit(request_id='retry')
        self.assertEqual(retry['status'], 'accepted')
        self.assertFalse(retry['duplicate'])
        self.assertEqual(self.count(), 1)

    async def test_ack_lost_after_actual_commit_and_receiver_restart_is_duplicate(self):
        await self.page.send(json.dumps({'type': 'adapter_submission', 'request_id': 'lost-ack', 'envelope': self.envelope}))
        frame = await recv_until(self.core, lambda message: message.get('type') == 'submission')
        unsent_ack = await self.receiver.handle_frame(frame)
        self.assertEqual(unsent_ack['status'], 'accepted')
        original = await self.store.get_message_by_submission_id(c.SubmissionId(self.envelope['submission_id']))
        await self.store.close()
        self.store = SQLiteMessageStore(self.database_path, RegistryEndpointIdentityReader(self.registry_path))
        self.receiver = CoreReceiver(self.store)
        await self.start_receiver()
        retried = await self.submit(request_id='retry-after-restart')
        self.assertTrue(retried['duplicate'])
        self.assertEqual(self.count(), 1)
        restored = await self.store.get_message_by_submission_id(c.SubmissionId(self.envelope['submission_id']))
        self.assertEqual(restored.message_id, original.message_id)

    async def test_registry_identity_failure_is_rejected_through_real_bridge(self):
        await self.start_receiver()
        bad = copy.deepcopy(self.envelope)
        bad['source']['native_session_id'] = 'not-bound'
        ack = await self.submit(bad, 'bad-source')
        self.assertEqual(ack['status'], 'rejected')
        self.assertEqual(ack['error']['code'], 'SOURCE_SESSION_MISMATCH')
        self.assertEqual(self.count(), 0)

    async def test_attempt_id_prevents_old_retry_ack_from_confirming_changed_body(self):
        await self.page.send(json.dumps({'type': 'adapter_submission', 'request_id': 'first-attempt', 'envelope': self.envelope}))
        first_frame = await recv_until(self.core, lambda message: message.get('type') == 'submission')
        await self.page.send(json.dumps({'type': 'adapter_submission', 'request_id': 'second-attempt', 'envelope': self.envelope}))
        second_frame = await recv_until(self.core, lambda message: message.get('type') == 'submission')
        first_ack = await self.receiver.handle_frame(first_frame)
        second_ack = await self.receiver.handle_frame(second_frame)
        await self.core.send(json.dumps(first_ack))
        with self.assertRaises(asyncio.TimeoutError):
            await recv_until(self.page, lambda message: message.get('type') == 'submission_ack', 0.2)
        await self.core.send(json.dumps(second_ack))
        accepted = await recv_until(self.page, lambda message: message.get('type') == 'submission_ack')
        self.assertEqual(accepted['request_id'], 'second-attempt')
        changed = copy.deepcopy(self.envelope)
        changed['content']['text'] = 'Changed after retry acceptance'
        await self.page.send(json.dumps({'type': 'adapter_submission', 'request_id': 'changed-attempt', 'envelope': changed}))
        changed_frame = await recv_until(self.core, lambda message: message.get('type') == 'submission')
        changed_ack = await self.receiver.handle_frame(changed_frame)
        self.assertEqual(changed_ack['status'], 'conflict')
        await self.core.send(json.dumps(first_ack))
        await self.core.send(json.dumps(changed_ack))
        result = await recv_until(self.page, lambda message: message.get('type') == 'submission_ack')
        self.assertEqual(result['request_id'], 'changed-attempt')
        self.assertEqual(result['status'], 'conflict')
        self.assertEqual(self.count(), 1)

    async def test_replacement_receiver_can_validate_corrected_previously_unprocessed_input(self):
        invalid = copy.deepcopy(self.envelope)
        invalid['source']['native_session_id'] = 'unbound-source'
        await self.page.send(json.dumps({'type': 'adapter_submission', 'request_id': 'unprocessed-old-source', 'envelope': invalid}))
        await recv_until(self.core, lambda message: message.get('type') == 'submission')
        self.assertEqual(self.count(), 0)
        await self.start_receiver()
        result = await self.submit(request_id='corrected-new-source')
        self.assertEqual(result['status'], 'accepted')
        self.assertFalse(result['duplicate'])
        self.assertEqual(self.count(), 1)

    async def test_cli_process_persists_and_restarts_without_duplicate(self):
        await self.core.close()
        for attempt in range(2):
            for _ in range(100):
                with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/health', timeout=1) as response:
                    if json.load(response)['external_clients'] == 0:
                        break
                await asyncio.sleep(0.02)
            env = dict(os.environ)
            env['CF_BRIDGE_TOKEN'] = TOKEN
            process = subprocess.Popen(
                [sys.executable, '-B', '-m', 'contextflow_core.receiver', '--once',
                 '--database', str(self.database_path), '--registry', str(self.registry_path),
                 '--uri', f'ws://127.0.0.1:{self.port}/ws', '--origin', self.origin],
                cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            try:
                connected = False
                for _ in range(150):
                    if process.poll() is not None:
                        break
                    with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/health', timeout=1) as response:
                        if json.load(response)['external_clients'] == 1:
                            connected = True
                            break
                    await asyncio.sleep(0.02)
                if not connected:
                    self.fail('CLI did not establish its receiver connection')
                ack = await self.submit(request_id=f'cli-{attempt}')
                stdout, stderr = await asyncio.to_thread(process.communicate, timeout=5)
                self.assertEqual(process.returncode, 0, stderr.decode('utf-8', errors='replace'))
                self.assertEqual(ack['status'], 'accepted')
                self.assertEqual(ack['duplicate'], attempt == 1)
                self.assertEqual(self.count(), 1)
            finally:
                if process.poll() is None:
                    process.terminate()
                await asyncio.to_thread(process.communicate, timeout=5)

    async def test_formal_receiver_is_rejected_if_mock_or_other_external_is_active(self):
        with self.assertRaises(ContextFlowError) as caught:
            await self.receiver.run(f'ws://127.0.0.1:{self.port}/ws?token={TOKEN}', self.origin)
        self.assertEqual(caught.exception.code, 'CORE_CONNECTION_REJECTED')
        self.assertEqual(self.count(), 0)
