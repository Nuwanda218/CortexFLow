"""Explicit composition CLI for the isolated Parser Worker."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
import json
import logging
from pathlib import Path
import sys
from typing import Any

from .agent_directory import ConfiguredAgentDirectory
from .anthropic_provider import AnthropicExtractionAdapter, AnthropicProviderConfig
from .endpoint_identity import RegistryEndpointIdentityReader
from .errors import ContextFlowError
from .fake_provider import FakeExtractionAdapter
from .instruction_parser import LlmInstructionParser
from .message_store import SQLiteMessageStore
from .openai_provider import (
    OpenAICompatibleExtractionAdapter,
    OpenAIProviderConfig,
)
from .parse_jobs import SQLiteParseJobRepository
from .parser_worker import CycleStatus, ParserWorker, ParserWorkerConfig

logger = logging.getLogger(__name__)


async def _run(args: argparse.Namespace) -> int:
    directory = ConfiguredAgentDirectory.from_file(args.directory)
    async with AsyncExitStack() as stack:
        provider: (
            FakeExtractionAdapter
            | AnthropicExtractionAdapter
            | OpenAICompatibleExtractionAdapter
        )
        if args.provider == "fake":
            raw = _strict_json_file(Path(args.fake_response), "Fake Provider response")
            provider = FakeExtractionAdapter((raw,))
            logger.warning("FAKE Parser Provider is active; use only an isolated database")
        elif args.provider == "anthropic":
            provider = AnthropicExtractionAdapter(
                AnthropicProviderConfig.from_environment(
                    timeout_seconds=args.provider_timeout,
                    max_tokens=args.max_tokens,
                )
            )
            stack.push_async_callback(provider.close)
        else:
            provider = OpenAICompatibleExtractionAdapter(
                OpenAIProviderConfig.from_environment(
                    timeout_seconds=args.provider_timeout,
                    max_tokens=args.max_tokens,
                )
            )
            stack.push_async_callback(provider.close)

        identities = RegistryEndpointIdentityReader(args.registry)
        store = SQLiteMessageStore(
            args.database,
            identities,
            busy_timeout=args.busy_timeout,
        )
        stack.push_async_callback(store.close)
        repository = SQLiteParseJobRepository(
            args.database,
            busy_timeout=args.busy_timeout,
        )
        stack.push_async_callback(repository.close)
        parser = LlmInstructionParser(provider, directory)
        worker = ParserWorker(
            repository=repository,
            messages=store,
            parser=parser,
            known_agents=parser.trusted_selectors(),
            config=ParserWorkerConfig(
                lease_duration_ms=args.lease_ms,
                execution_timeout_ms=args.execution_timeout_ms,
                submit_margin_ms=args.submit_margin_ms,
                cancel_grace_ms=args.cancel_grace_ms,
                max_attempts=args.max_attempts,
                retry_base_ms=args.retry_base_ms,
                retry_cap_ms=args.retry_cap_ms,
                discover_limit=args.discover_limit,
                poll_interval_ms=args.poll_interval_ms,
            ),
        )
        if args.once:
            outcome = await worker.run_once()
            logger.info(
                "Parser cycle status=%s message_id=%s parse_status=%s error=%s",
                outcome.status.value,
                outcome.message_id,
                outcome.parse_status.value if outcome.parse_status else None,
                outcome.error.code if outcome.error else None,
            )
            return 0
        await worker.run()
        return 0


def _strict_json_file(path: Path, label: str) -> object:
    try:
        text = path.expanduser().read_text(encoding="utf-8")
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise ContextFlowError(
            "INVALID_ARGUMENT",
            f"{label} must be a readable strict JSON file",
            details={"field": str(path), "exception_type": type(exc).__name__},
        ) from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ContextFlow Parser Worker: Message SQLite to formal ParseResult"
    )
    parser.add_argument("--database", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--directory", required=True)
    parser.add_argument(
        "--provider",
        choices=("fake", "anthropic", "openai-compatible"),
        required=True,
    )
    parser.add_argument("--fake-response")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--busy-timeout", type=float, default=5.0)
    parser.add_argument("--provider-timeout", type=float, default=20.0)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--lease-ms", type=int, default=30_000)
    parser.add_argument("--execution-timeout-ms", type=int, default=10_000)
    parser.add_argument("--submit-margin-ms", type=int, default=5_000)
    parser.add_argument("--cancel-grace-ms", type=int, default=1_000)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--retry-base-ms", type=int, default=1_000)
    parser.add_argument("--retry-cap-ms", type=int, default=30_000)
    parser.add_argument("--discover-limit", type=int, default=100)
    parser.add_argument("--poll-interval-ms", type=int, default=500)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.provider == "fake" and not args.fake_response:
        parser.error("--fake-response is required for --provider fake")
    if args.provider != "fake" and args.fake_response:
        logger.error("--fake-response can only be used with --provider fake")
        return 2
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
    except ContextFlowError as exc:
        logger.error("%s: %s", exc.code, exc.message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
