"""CLI entry-point tests (no real Agent execution)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "contextflow_core.agent_runtime.cli", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def test_cli_list_outputs_capability_matrix(tmp_path: Path) -> None:
    result = _run_cli("--journal-root", str(tmp_path / "journal"), "list")
    assert result.returncode == 0, result.stderr
    matrix = json.loads(result.stdout)
    assert {row["runtime"] for row in matrix} == {
        "codex-cli",
        "claude-cli",
        "codex-relay",
        "dsh",
        "codex-desktop",
    }


def test_cli_probe_desktop(tmp_path: Path) -> None:
    result = _run_cli(
        "--journal-root", str(tmp_path / "journal"), "probe", "--runtime", "codex-desktop"
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["runtime"] == "codex-desktop"
    assert payload["capabilities"]["control_kind"] == "gui"


def test_cli_unsupported_runtime_returns_error(tmp_path: Path) -> None:
    result = _run_cli(
        "--journal-root",
        str(tmp_path / "journal"),
        "run",
        "--runtime",
        "codex-desktop",
        "--workdir",
        str(tmp_path),
        "--instruction",
        "hello",
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "ADAPTER_UNSUPPORTED"


def test_cli_unknown_execution_returns_error(tmp_path: Path) -> None:
    result = _run_cli(
        "--journal-root",
        str(tmp_path / "journal"),
        "inspect",
        "--runtime",
        "codex-cli",
        "--execution-id",
        "cfexec_unknown",
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "ADAPTER_EXECUTION_NOT_FOUND"
