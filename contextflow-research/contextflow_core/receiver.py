"""Supervisor Bridge ingress backed by MessageStore; no parsing or execution."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
import json
import logging
import math
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus, InvalidURI

from .contracts import (
    AcceptResult, AcceptStatus, DeliveryId, DeliveryOutcome, DeliveryState, EndpointId,
    ErrorInfo, MessageStore, NativeSessionId, SupervisorSubmission,
)
from .endpoint_identity import RegistryEndpointIdentityReader
from .errors import ContextFlowError
from .message_store import SQLiteMessageStore, default_message_store_path
from .parse_jobs import SQLiteParseJobRepository
from .registry import EndpointRegistry, default_registry_path
from .submission import accepted_ack, rejected_ack
from .supervisor_control import SupervisorControlService
from .task_reports import TaskReportService
from .task_store import SQLiteTaskRepository

logger = logging.getLogger(__name__)


def bridge_connection_settings(uri: str | None = None, origin: str | None = None) -> tuple[str, str]:
    uri = uri or os.environ.get('CONTEXTFLOW_BRIDGE_URI')
    if not uri:
        raise ContextFlowError('INVALID_ARGUMENT', 'configure --uri or CONTEXTFLOW_BRIDGE_URI')
    try:
        parts = urlsplit(uri)
        if parts.scheme not in ('ws', 'wss') or not parts.hostname or parts.username is not None or parts.password is not None or parts.fragment:
            raise ValueError('invalid Bridge URI')
        parts.port
        query = parse_qsl(parts.query, keep_blank_values=True)
        tokens = [value for key, value in query if key == 'token']
        if len(tokens) > 1:
            raise ValueError('ambiguous token')
        if not tokens:
            token = os.environ.get('CF_BRIDGE_TOKEN')
            if not token:
                raise ValueError('missing token')
            query.append(('token', token))
        elif not tokens[0]:
            raise ValueError('empty token')
        uri = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ''))
    except (ValueError, TypeError):
        raise ContextFlowError('INVALID_ARGUMENT', 'Bridge URI must use ws/wss with one configured token and no embedded credentials') from None
    origin = origin or os.environ.get('CONTEXTFLOW_BRIDGE_ORIGIN')
    if origin is None:
        origin = ('https' if parts.scheme == 'wss' else 'http') + '://' + parts.netloc
    return uri, origin


def _timeout(value: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ContextFlowError('INVALID_ARGUMENT', f'{field} must be a positive finite number')
    return float(value)


def _valid_control_result(value: object) -> bool:
    if not isinstance(value, dict) or type(value.get('success')) is not bool:
        return False
    expected = {'success', 'data'} if value['success'] else {'success', 'error'}
    if set(value) != expected:
        return False
    if value['success']:
        return isinstance(value['data'], dict)
    error = value['error']
    return (
        isinstance(error, dict)
        and set(error) == {'code', 'message'}
        and isinstance(error['code'], str)
        and bool(error['code'])
        and isinstance(error['message'], str)
        and bool(error['message'])
    )


class CoreReceiver:
    def __init__(
        self,
        store: MessageStore,
        *,
        controls: SupervisorControlService | None = None,
        ack_timeout: float = 5.0,
        connection_timeout: float = 5.0,
    ) -> None:
        self.store = store
        if controls is not None and not callable(getattr(controls, 'handle', None)):
            raise ContextFlowError('INVALID_ARGUMENT', 'controls must provide an async handle operation')
        self.controls = controls
        self.ack_timeout = _timeout(ack_timeout, 'ack_timeout')
        self.connection_timeout = _timeout(connection_timeout, 'connection_timeout')
        self._bridge_socket = None
        self._send_replies: dict[str, asyncio.Future] = {}

    async def receive(self, envelope: object) -> AcceptResult:
        try:
            result = await self.store.accept_message(SupervisorSubmission(envelope=envelope))
            if not isinstance(result, AcceptResult):
                raise TypeError('invalid Store result')
        except ContextFlowError as exc:
            result = AcceptResult(status=AcceptStatus.REJECTED, error=ErrorInfo.from_error(exc))
        except Exception as exc:
            result = AcceptResult(status=AcceptStatus.REJECTED, error=ErrorInfo(
                code='CORE_RECEIVER_ERROR', message='receiver did not confirm durable acceptance',
                details={'exception_type': type(exc).__name__},
            ))
        logger.info('reception status=%s message_id=%s duplicate=%s error=%s', result.status.value, result.message_id, result.duplicate, result.error.code if result.error else None)
        return result

    async def handle_frame(self, frame: object) -> dict | None:
        if not isinstance(frame, dict):
            return None
        if frame.get('type') == 'supervisor_control_request':
            return await self._handle_control_frame(frame)
        if frame.get('type') != 'submission':
            return None
        envelope = frame.get('envelope')
        request_id = frame.get('request_id')
        submission_id = envelope.get('submission_id') if isinstance(envelope, dict) else None
        submission_id = submission_id if isinstance(submission_id, str) else None
        result = await self.receive(envelope)
        if result.status is AcceptStatus.ACCEPTED:
            ack = accepted_ack(submission_id, duplicate=result.duplicate)
        else:
            error = result.error.to_dict()
            ack = rejected_ack(submission_id, error['code'], error['message'], details=error['details'])
            ack['status'] = result.status.value
        result_frame = {
            'type': 'submission_ack', 'submission_id': ack['submission_id'],
            'status': ack['status'], 'duplicate': ack.get('duplicate', False), 'error': ack.get('error'),
        }
        if request_id is not None:
            result_frame['request_id'] = request_id
        return result_frame

    async def _handle_control_frame(self, frame: dict) -> dict:
        raw_request_id = frame.get('request_id')
        request_id = (
            raw_request_id
            if isinstance(raw_request_id, str) and bool(raw_request_id)
            else None
        )
        base = {
            'type': 'supervisor_control_result',
            'request_id': request_id,
        }
        if (
            set(frame) != {'type', 'request_id', 'action', 'params'}
            or not isinstance(raw_request_id, str)
            or not raw_request_id
        ):
            return {
                **base,
                'success': False,
                'error': {
                    'code': 'INVALID_CONTROL_REQUEST',
                    'message': 'invalid Supervisor control request',
                },
            }
        if self.controls is None:
            return {
                **base,
                'success': False,
                'error': {
                    'code': 'CORE_CONTROL_UNAVAILABLE',
                    'message': 'Supervisor control service is unavailable',
                },
            }
        try:
            result = await self.controls.handle(frame['action'], frame['params'])
        except asyncio.CancelledError:
            raise
        except Exception:
            result = {
                'success': False,
                'error': {
                    'code': 'CORE_CONTROL_ERROR',
                    'message': 'Supervisor control operation failed',
                },
            }
        if not _valid_control_result(result):
            result = {
                'success': False,
                'error': {
                    'code': 'CORE_CONTROL_ERROR',
                    'message': 'Supervisor control operation failed',
                },
            }
        return {**base, **result}

    async def serve_connection(self, socket, *, once: bool = False, ready: asyncio.Event | None = None) -> int:
        self._bridge_socket = socket
        try:
            return await self._serve_frames(socket, once=once, ready=ready)
        finally:
            self._bridge_socket = None
            for future in self._send_replies.values():
                if not future.done():
                    future.set_result(None)

    async def send_message_to_endpoint(
        self, endpoint_id: EndpointId, native_session_id: NativeSessionId, text: str,
    ) -> DeliveryOutcome:
        socket = self._bridge_socket
        if socket is None:
            return DeliveryOutcome(state=DeliveryState.PENDING, error=ErrorInfo(
                code='BRIDGE_UNAVAILABLE', message='Core Receiver is not connected',
            ))
        request_id = f'report-send-{uuid4().hex}'
        future = asyncio.get_running_loop().create_future()
        self._send_replies[request_id] = future
        try:
            request = {
                'type': 'send_message_to_endpoint', 'id': request_id, 'endpoint_id': str(endpoint_id),
                'expected_native_session_id': str(native_session_id), 'text': text, 'force': False,
            }
            await asyncio.wait_for(socket.send(json.dumps(request, ensure_ascii=False)), self.ack_timeout)
            response = await asyncio.wait_for(future, timeout=50)
        except (ConnectionClosed, OSError, asyncio.TimeoutError):
            response = None
        finally:
            self._send_replies.pop(request_id, None)
        return _report_send_outcome(response, native_session_id)

    async def _serve_frames(self, socket, *, once: bool, ready: asyncio.Event | None) -> int:
        accepted = 0
        async for raw in socket:
            try:
                frame = json.loads(raw)
            except (ValueError, TypeError, UnicodeError, RecursionError):
                logger.warning('ignored malformed Bridge frame')
                continue
            if isinstance(frame, dict) and frame.get('type') == 'welcome':
                if ready is not None:
                    ready.set()
                logger.info('Core Receiver connected')
                continue
            if isinstance(frame, dict) and frame.get('type') == 'result':
                request_id = frame.get('id')
                future = self._send_replies.get(request_id) if isinstance(request_id, str) else None
                if future is not None and not future.done():
                    future.set_result(frame)
                continue
            ack = await self.handle_frame(frame)
            if ack is None:
                continue
            try:
                await asyncio.wait_for(socket.send(json.dumps(ack, ensure_ascii=False)), self.ack_timeout)
            except asyncio.TimeoutError as exc:
                # The transaction result is final even if the confirmation is lost.
                raise ContextFlowError('ACK_SEND_TIMEOUT', 'confirmation send timed out; retry the original submission', retryable=True) from exc
            if (
                ack.get('type') == 'submission_ack'
                and ack.get('status') == 'accepted'
            ):
                accepted += 1
                if once:
                    break
        return accepted

    async def run(
        self, uri: str, origin: str | None = None, *, once: bool = False,
        ready: asyncio.Event | None = None,
    ) -> int:
        uri, origin = bridge_connection_settings(uri, origin)
        try:
            # Envelope limits count characters; encoded frames may exceed 1 MiB.
            async with websockets.connect(uri, origin=origin, max_size=None, open_timeout=self.connection_timeout, proxy=None) as socket:
                return await self.serve_connection(socket, once=once, ready=ready)
        except InvalidURI:
            raise ContextFlowError('INVALID_ARGUMENT', 'invalid Bridge URI; credentials are not included in diagnostics') from None
        except ConnectionClosed as exc:
            code = exc.rcvd.code if exc.rcvd is not None else None
            if code == 4409:
                raise ContextFlowError('CORE_CONNECTION_REJECTED', 'Bridge already has an external Core connection; stop the Mock or other client first') from None
            raise ContextFlowError('BRIDGE_DISCONNECTED', 'Bridge connection closed; committed messages remain stored', retryable=True) from None
        except InvalidStatus:
            raise ContextFlowError('BRIDGE_ACCESS_DENIED', 'Bridge rejected the connection; check token and Origin configuration') from None
        except (OSError, asyncio.TimeoutError):
            raise ContextFlowError('BRIDGE_UNAVAILABLE', 'cannot establish a Bridge connection', retryable=True) from None


def _report_send_outcome(response, native_session_id: NativeSessionId) -> DeliveryOutcome:
    if isinstance(response, dict):
        data = response.get('data')
        if response.get('success') is True and isinstance(data, dict):
            message_id = data.get('user_message_id')
            if (data.get('sent') is True and data.get('verified') is True
                    and data.get('session_id') == native_session_id
                    and isinstance(message_id, str) and message_id.strip()):
                return DeliveryOutcome(state=DeliveryState.CONFIRMED, evidence={
                    'session_id': str(native_session_id), 'user_message_id': message_id,
                })
        error = response.get('error')
        code = error.get('code') if isinstance(error, dict) else None
        if code in ('ENDPOINT_NOT_FOUND', 'ENDPOINT_SESSION_MISMATCH', 'DRAFT_PRESENT',
                    'REPORT_PAGE_UNAVAILABLE', 'REPORT_SEND_BUSY', 'SESSION_NOT_FOUND', 'AMBIGUOUS_SESSION'):
            return DeliveryOutcome(state=DeliveryState.PENDING, error=ErrorInfo(
                code=code, message='report was not sent; check the original session and its composer',
            ))
        if code == 'INVALID_REQUEST':
            return DeliveryOutcome(state=DeliveryState.FAILED, error=ErrorInfo(
                code=code, message='Bridge rejected the report request',
            ))
        if code == 'SEND_UNVERIFIED':
            return DeliveryOutcome(state=DeliveryState.UNKNOWN, error=ErrorInfo(
                code=code, message='browser did not confirm the report',
            ))
    return DeliveryOutcome(state=DeliveryState.UNKNOWN, error=ErrorInfo(
        code='REPORT_SEND_UNKNOWN', message='no verified receipt from the original Supervisor session',
    ))


async def _run_cli(args) -> int:
    uri, origin = bridge_connection_settings(args.uri, args.origin)
    registry = EndpointRegistry(args.registry)
    identities = RegistryEndpointIdentityReader(args.registry)
    async with AsyncExitStack() as stack:
        store = SQLiteMessageStore(
            args.database,
            identities,
            busy_timeout=args.busy_timeout,
            identity_timeout=args.identity_timeout,
        )
        stack.push_async_callback(store.close)
        parse_jobs = SQLiteParseJobRepository(
            args.database,
            busy_timeout=args.busy_timeout,
        )
        stack.push_async_callback(parse_jobs.close)
        reports = TaskReportService(SQLiteTaskRepository(args.database), store, identities)
        controls = SupervisorControlService(registry, store, parse_jobs, reports=reports)
        receiver = CoreReceiver(
            store,
            controls=controls,
            ack_timeout=args.ack_timeout,
            connection_timeout=args.connection_timeout,
        )
        delivery_id = getattr(args, 'return_delivery_id', None)
        if delivery_id is None:
            await receiver.run(uri, origin, once=args.once)
            return 0
        report = await reports.generate_report(DeliveryId(delivery_id))
        ready = asyncio.Event()
        serving = asyncio.create_task(receiver.run(uri, origin, ready=ready))

        async def send_when_ready():
            await ready.wait()
            returned = await reports.send_report(report.report_id, receiver)
            logger.info('report=%s delivery=%s review=%s', returned.report_id, returned.delivery.state.value,
                        returned.review_state)

        sending = asyncio.create_task(send_when_ready())
        try:
            done, _ = await asyncio.wait((serving, sending), return_when=asyncio.FIRST_COMPLETED)
            if sending in done:
                sending.result()
            await serving
        finally:
            serving.cancel()
            sending.cancel()
            await asyncio.gather(serving, sending, return_exceptions=True)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='ContextFlow Supervisor Receiver: Bridge to SQLite, without task execution')
    parser.add_argument('--database', default=str(default_message_store_path()))
    parser.add_argument('--registry', default=str(default_registry_path()))
    parser.add_argument('--uri', help='Bridge /ws URI, or use CONTEXTFLOW_BRIDGE_URI')
    parser.add_argument('--origin', help='Bridge Origin, or use CONTEXTFLOW_BRIDGE_ORIGIN')
    parser.add_argument('--busy-timeout', type=float, default=5.0)
    parser.add_argument('--identity-timeout', type=float, default=5.0)
    parser.add_argument('--ack-timeout', type=float, default=5.0)
    parser.add_argument('--connection-timeout', type=float, default=5.0)
    parser.add_argument('--once', action='store_true', help='exit after the first accepted submission')
    parser.add_argument('--return-delivery-id', help='return the saved execution report, then listen for Supervisor review')
    args = parser.parse_args()
    if args.once and args.return_delivery_id:
        parser.error('--return-delivery-id requires a persistent connection; omit --once')
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    try:
        return asyncio.run(_run_cli(args))
    except KeyboardInterrupt:
        return 130
    except ContextFlowError as exc:
        logger.error('%s: %s', exc.code, exc.message)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
