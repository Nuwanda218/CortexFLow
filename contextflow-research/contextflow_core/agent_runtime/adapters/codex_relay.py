"""Codex-Relay CLI runtime adapter.

Codex-Relay is not a separate program or protocol: it is a wrapper that sets
``CODEX_HOME`` to an isolated directory and calls the same official Codex CLI
binary. It differs from Codex CLI only in provider, credentials, config and
session store. Therefore this adapter reuses the Codex CLI implementation and
adds:

* an isolated ``CODEX_HOME`` (never the desktop/CLI default);
* ``max_concurrency=1`` because the relay channel rejects concurrent
  conversations with HTTP 403 ("Only one Codex conversation can run at a time");
* a separate journal directory (configured by the registry), so relay
  executions never mix with Codex CLI executions.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..contracts import AgentCapabilities, ControlKind, EvidenceStatus
from .codex_cli import SANDBOX_BY_PROFILE, CodexCliAdapter

DEFAULT_RELAY_COMMAND = r"D:\codex\codex-relay.cmd"


def default_relay_command() -> list[str]:
    configured = os.environ.get("CF_C03_RELAY_COMMAND")
    if configured:
        return [configured]
    if Path(DEFAULT_RELAY_COMMAND).exists():
        return [DEFAULT_RELAY_COMMAND]
    found = shutil.which("codex-relay")
    return [found or "codex-relay"]


class CodexRelayAdapter(CodexCliAdapter):
    runtime = "codex-relay"
    capabilities = AgentCapabilities(
        runtime="codex-relay",
        control_kind=ControlKind.PROCESS,
        supports_new_session=True,
        supports_session_reuse=True,
        supports_resume=True,
        supports_streaming=True,
        supports_structured_result=True,
        supports_structured_failure=True,
        supports_native_cancel=False,
        supports_usage=True,
        supports_permission_profiles=True,
        supports_workspace_isolation=True,
        supported_permission_profiles=tuple(SANDBOX_BY_PROFILE),
        max_concurrency=1,
        evidence_status=EvidenceStatus.VERIFIED,
        notes=(
            "same official codex binary as codex-cli, isolated CODEX_HOME",
            "third-party relay provider with channel API-key auth",
            "single-conversation channel limit: max_concurrency=1",
            "HTTP 403 single-conversation errors map to CONCURRENCY_LIMIT",
            "relay sessions are NOT shared with Codex CLI or the desktop app",
        ),
    )

    def __init__(
        self,
        journal_dir: str | Path,
        command: list[str] | None = None,
        codex_home: str | None = None,
        env: dict[str, str] | None = None,
        max_concurrency: int | None = 1,
    ) -> None:
        home = codex_home or os.environ.get(
            "CF_C03_RELAY_CODEX_HOME",
            str(Path.home() / ".codex-cli-relay"),
        )
        merged_env = {
            "CODEX_HOME": home,
            "OPENAI_API_KEY": "",
            "OPENAI_BASE_URL": "",
            "CODEX_ACCESS_TOKEN": "",
            "CODEX_API_KEY": "",
        }
        if env:
            merged_env.update(env)
        super().__init__(
            journal_dir,
            command=command or default_relay_command(),
            env=merged_env,
            max_concurrency=max_concurrency,
        )
        self.codex_home = home
