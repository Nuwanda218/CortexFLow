"""Standalone verification CLI for the Agent Runtime adapter layer.

This entry point exists to exercise adapters through the unified interface. It
does **not** create Tasks, Deliveries or Dispatcher state.

Examples::

    python -m contextflow_core.agent_runtime.cli list
    python -m contextflow_core.agent_runtime.cli probe --runtime codex-cli
    python -m contextflow_core.agent_runtime.cli run --runtime claude-cli \
        --workdir D:\\tmp\\c03-claude --instruction "create hello.txt ..." --timeout 180
    python -m contextflow_core.agent_runtime.cli resume --runtime dsh \
        --session session-... --workdir D:\\tmp\\c03-dsh --instruction "continue"
    python -m contextflow_core.agent_runtime.cli inspect --runtime codex-cli \
        --execution-id cfexec_...
    python -m contextflow_core.agent_runtime.cli cancel --runtime dsh \
        --execution-id cfexec_...

Exit codes: 0 finished, 3 failed, 4 cancelled, 5 unknown, 1 adapter/usage error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

from .config import RuntimeConfig, load_config
from .contracts import ExecutionRequest, SessionPolicy
from .errors import AdapterError, InvalidRequest
from .registry import RUNTIME_KINDS, RuntimeRegistry, create_registry

EXIT_BY_STATE = {
    "finished": 0,
    "failed": 3,
    "cancelled": 4,
    "unknown": 5,
}


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m contextflow_core.agent_runtime.cli",
        description="Verify Agent Runtime adapters through the unified interface (no Task/Delivery).",
    )
    parser.add_argument("--config", default=None, help="optional runtime config JSON")
    parser.add_argument("--journal-root", default=None, help="override the execution journal root")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="print the capability matrix")

    probe = sub.add_parser("probe", help="probe one runtime")
    probe.add_argument("--runtime", choices=RUNTIME_KINDS, required=True)

    run = sub.add_parser("run", help="start one execution and wait for its result")
    run.add_argument("--runtime", choices=RUNTIME_KINDS, required=True)
    run.add_argument("--workdir", required=True)
    run.add_argument("--instruction", default=None)
    run.add_argument("--instruction-file", default=None)
    run.add_argument("--caller-ref", default=None)
    run.add_argument("--session-policy", choices=[item.value for item in SessionPolicy], default="auto")
    run.add_argument("--session", default=None, help="native session id to reuse")
    run.add_argument("--timeout", type=float, default=300.0)
    run.add_argument("--permission-profile", default="workspace_write")
    run.add_argument("--out", default=None, help="write the JSON result to this file")

    resume = sub.add_parser("resume", help="run against an existing native session")
    resume.add_argument("--runtime", choices=RUNTIME_KINDS, required=True)
    resume.add_argument("--session", required=True)
    resume.add_argument("--workdir", required=True)
    resume.add_argument("--instruction", default=None)
    resume.add_argument("--instruction-file", default=None)
    resume.add_argument("--caller-ref", default=None)
    resume.add_argument("--timeout", type=float, default=300.0)
    resume.add_argument("--permission-profile", default="workspace_write")
    resume.add_argument("--out", default=None)

    inspect = sub.add_parser("inspect", help="inspect one execution")
    inspect.add_argument("--runtime", choices=RUNTIME_KINDS, required=True)
    inspect.add_argument("--execution-id", required=True)

    cancel = sub.add_parser("cancel", help="cancel one execution")
    cancel.add_argument("--runtime", choices=RUNTIME_KINDS, required=True)
    cancel.add_argument("--execution-id", required=True)

    return parser


def _config_from_args(args: argparse.Namespace) -> RuntimeConfig:
    config = load_config(args.config)
    if args.journal_root:
        config = config.with_overrides(journal_root=Path(args.journal_root))
    return config


def _instruction_from_args(args: argparse.Namespace) -> str:
    if args.instruction and args.instruction_file:
        raise InvalidRequest("provide exactly one of --instruction and --instruction-file")
    if args.instruction_file:
        return Path(args.instruction_file).read_text(encoding="utf-8")
    if args.instruction:
        return args.instruction
    raise InvalidRequest("an instruction is required")


async def _run_command(args: argparse.Namespace, registry: RuntimeRegistry) -> int:
    adapter = registry.get(args.runtime)
    if args.command == "resume":
        policy = SessionPolicy.REUSE
        session = args.session
    else:
        policy = SessionPolicy(args.session_policy)
        session = args.session
    request = ExecutionRequest(
        runtime=args.runtime,
        instruction=_instruction_from_args(args),
        workdir=str(Path(args.workdir).resolve()),
        caller_ref=args.caller_ref or ("cfcall_" + uuid.uuid4().hex),
        session_policy=policy,
        native_session_id=session,
        timeout_s=args.timeout,
        permission_profile=args.permission_profile,
    )
    handle = await adapter.start(request)
    result = await adapter.wait(handle.execution_id)
    payload = {"handle": handle.to_dict(), "result": result.to_dict()}
    if args.out:
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _print(payload)
    return EXIT_BY_STATE.get(result.state.value, 1)


async def _dispatch(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    registry = create_registry(config)
    if args.command == "list":
        _print(registry.capabilities_matrix())
        return 0
    adapter = registry.get(args.runtime)
    if args.command == "probe":
        _print(await adapter.probe())
        return 0
    if args.command == "inspect":
        _print((await adapter.inspect(args.execution_id)).to_dict())
        return 0
    if args.command == "cancel":
        _print((await adapter.cancel(args.execution_id)).to_dict())
        return 0
    return await _run_command(args, registry)


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_dispatch(args))
    except AdapterError as exc:
        _print({"error": exc.to_dict()})
        return 1
    except (OSError, ValueError) as exc:
        _print({"error": {"code": "CLI_ERROR", "message": str(exc), "retryable": False}})
        return 1


if __name__ == "__main__":
    sys.exit(main())
