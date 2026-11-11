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

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus, InvalidURI

from .contracts import AcceptResult, AcceptStatus, ErrorInfo, MessageStore, SupervisorSubmission
from .endpoint_identity import RegistryEndpointIdentityReader
from .errors import ContextFlowError
from .message_store import SQLiteMessageStore, default_message_store_path
from .parse_jobs import SQLiteParseJobRepository
from .registry import EndpointRegistry, default_registry_path
from .submission import accepted_ack, rejected_ack
from .supervisor_control import SupervisorControlService

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
        controls = SupervisorControlService(registry, store, parse_jobs)
        await CoreReceiver(
            store,
            controls=controls,
            ack_timeout=args.ack_timeout,
            connection_timeout=args.connection_timeout,
        ).run(uri, origin, once=args.once)
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
    args = parser.parse_args()
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
