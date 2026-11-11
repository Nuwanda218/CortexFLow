"""Configuration for the Agent Runtime adapter layer.

Secrets are never stored here. Credentials come from the Agent's own existing
configuration (Codex OAuth store, Claude OAuth store, Relay channel key file,
DSH launcher state) or from environment variables owned by those tools.

Precedence: explicit JSON file > environment variables > defaults.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


def _default_journal_root() -> Path:
    configured = os.environ.get("CF_C03_JOURNAL_ROOT")
    if configured:
        return Path(configured)
    return Path.home() / ".contextflow" / "agent-runtime" / "journal"


@dataclass(frozen=True)
class RuntimeConfig:
    journal_root: Path = field(default_factory=_default_journal_root)
    codex_command: tuple[str, ...] = ("codex",)
    claude_command: tuple[str, ...] = ("claude",)
    relay_command: tuple[str, ...] = ()
    relay_codex_home: str | None = None
    dsh_launcher_state: str | None = None
    dsh_poll_interval: float = 2.0
    codex_max_concurrency: int | None = None
    claude_max_concurrency: int | None = None
    relay_max_concurrency: int = 1
    dsh_max_concurrency: int | None = None

    def with_overrides(self, **kwargs: Any) -> "RuntimeConfig":
        return replace(self, **kwargs)


def load_config(path: str | Path | None = None) -> RuntimeConfig:
    """Load config from defaults, environment, then an optional JSON file."""
    config = RuntimeConfig()
    env = os.environ
    overrides: dict[str, Any] = {}
    if env.get("CF_C03_CODEX_COMMAND"):
        overrides["codex_command"] = (env["CF_C03_CODEX_COMMAND"],)
    if env.get("CF_C03_CLAUDE_COMMAND"):
        overrides["claude_command"] = (env["CF_C03_CLAUDE_COMMAND"],)
    if env.get("CF_C03_RELAY_COMMAND"):
        overrides["relay_command"] = (env["CF_C03_RELAY_COMMAND"],)
    if env.get("CF_C03_RELAY_CODEX_HOME"):
        overrides["relay_codex_home"] = env["CF_C03_RELAY_CODEX_HOME"]
    if env.get("CF_C03_DSH_LAUNCHER_STATE"):
        overrides["dsh_launcher_state"] = env["CF_C03_DSH_LAUNCHER_STATE"]
    if env.get("CF_C03_DSH_POLL_INTERVAL"):
        try:
            overrides["dsh_poll_interval"] = float(env["CF_C03_DSH_POLL_INTERVAL"])
        except ValueError:
            pass
    if env.get("CF_C03_JOURNAL_ROOT"):
        overrides["journal_root"] = Path(env["CF_C03_JOURNAL_ROOT"])
    if overrides:
        config = config.with_overrides(**overrides)

    config_path = Path(path) if path else (
        Path(env["CF_C03_CONFIG"]) if env.get("CF_C03_CONFIG") else None
    )
    if config_path is not None and config_path.exists():
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        config = _apply_file_config(config, raw)
    return config


def _apply_file_config(config: RuntimeConfig, raw: dict[str, Any]) -> RuntimeConfig:
    if not isinstance(raw, dict):
        raise ValueError("runtime config must be a JSON object")
    overrides: dict[str, Any] = {}
    if "journal_root" in raw:
        overrides["journal_root"] = Path(str(raw["journal_root"]))
    if "dsh" in raw and isinstance(raw["dsh"], dict):
        dsh = raw["dsh"]
        if "launcher_state" in dsh:
            overrides["dsh_launcher_state"] = str(dsh["launcher_state"])
        if "poll_interval" in dsh:
            overrides["dsh_poll_interval"] = float(dsh["poll_interval"])
        if "max_concurrency" in dsh:
            overrides["dsh_max_concurrency"] = dsh["max_concurrency"]
    if "codex" in raw and isinstance(raw["codex"], dict):
        codex = raw["codex"]
        if "command" in codex:
            overrides["codex_command"] = tuple(str(item) for item in codex["command"])
        if "max_concurrency" in codex:
            overrides["codex_max_concurrency"] = codex["max_concurrency"]
    if "claude" in raw and isinstance(raw["claude"], dict):
        claude = raw["claude"]
        if "command" in claude:
            overrides["claude_command"] = tuple(str(item) for item in claude["command"])
        if "max_concurrency" in claude:
            overrides["claude_max_concurrency"] = claude["max_concurrency"]
    if "relay" in raw and isinstance(raw["relay"], dict):
        relay = raw["relay"]
        if "command" in relay:
            overrides["relay_command"] = tuple(str(item) for item in relay["command"])
        if "codex_home" in relay:
            overrides["relay_codex_home"] = str(relay["codex_home"])
        if "max_concurrency" in relay:
            overrides["relay_max_concurrency"] = int(relay["max_concurrency"])
    return config.with_overrides(**overrides)
