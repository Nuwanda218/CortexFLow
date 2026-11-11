"""Runtime registry: the future Dispatcher's adapter selection point.

A logical Agent in the Agent Directory names a runtime kind plus its own native
session reference. Codex CLI, Codex-Relay and the Codex desktop app are
different runtime kinds and must not be treated as interchangeable endpoints.
"""

from __future__ import annotations

from typing import Any, Mapping

from .adapters import ClaudeCliAdapter, CodexCliAdapter, CodexDesktopRuntime, CodexRelayAdapter
from .adapters.codex_relay import default_relay_command
from .adapters.dsh import DshRuntimeAdapter
from .base import AgentRuntimeAdapter
from .config import RuntimeConfig, load_config
from .errors import InvalidRequest

RUNTIME_KINDS = ("codex-cli", "claude-cli", "codex-relay", "dsh", "codex-desktop")


class RuntimeRegistry:
    def __init__(self, adapters: Mapping[str, AgentRuntimeAdapter]) -> None:
        self._adapters = dict(adapters)

    def get(self, runtime: str) -> AgentRuntimeAdapter:
        adapter = self._adapters.get(runtime)
        if adapter is None:
            raise InvalidRequest(
                f"unknown runtime {runtime!r}",
                details={"runtime": runtime, "known": list(self._adapters)},
            )
        return adapter

    def runtimes(self) -> tuple[str, ...]:
        return tuple(self._adapters)

    def capabilities_matrix(self) -> list[dict[str, Any]]:
        return [adapter.capabilities.to_dict() for adapter in self._adapters.values()]

    async def close_all(self) -> None:
        for adapter in self._adapters.values():
            try:
                await adapter.close()
            except Exception:
                pass


def create_registry(config: RuntimeConfig | None = None) -> RuntimeRegistry:
    config = config or load_config()
    journal_root = config.journal_root
    adapters: dict[str, AgentRuntimeAdapter] = {
        "codex-cli": CodexCliAdapter(
            journal_root / "codex-cli",
            command=list(config.codex_command),
            max_concurrency=config.codex_max_concurrency,
        ),
        "claude-cli": ClaudeCliAdapter(
            journal_root / "claude-cli",
            command=list(config.claude_command),
            max_concurrency=config.claude_max_concurrency,
        ),
        "codex-relay": CodexRelayAdapter(
            journal_root / "codex-relay",
            command=list(config.relay_command) or default_relay_command(),
            codex_home=config.relay_codex_home,
            max_concurrency=config.relay_max_concurrency,
        ),
        "dsh": DshRuntimeAdapter(
            journal_root / "dsh",
            launcher_state=config.dsh_launcher_state,
            poll_interval=config.dsh_poll_interval,
            max_concurrency=config.dsh_max_concurrency,
        ),
        "codex-desktop": CodexDesktopRuntime(),
    }
    return RuntimeRegistry(adapters)
