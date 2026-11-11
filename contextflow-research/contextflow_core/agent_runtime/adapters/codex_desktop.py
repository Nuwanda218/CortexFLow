"""Codex desktop application runtime descriptor (no automatic control).

C-02 findings (OpenAI.Codex 26.1103.7773.0):

* the desktop app is an Electron client whose backend is a bundled
  ``codex.exe app-server`` speaking the Codex app-server protocol over stdio;
* there is no documented local API or listening control socket an external
  process can attach to;
* it shares ``%USERPROFILE%\\.codex`` sessions and the durable queue with the
  CLI, but the control plane and execution process are not shared;
* ``codex://`` only launches/focuses the app; GUI automation is fragile.

This class is therefore a *descriptor*: it declares the runtime as unsupported
for automatic execution and exposes read-only diagnostics. It never fakes a
successful start.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from ..base import AgentRuntimeAdapter
from ..contracts import (
    AgentCapabilities,
    ControlKind,
    EvidenceStatus,
    ExecutionHandle,
    ExecutionRequest,
    ExecutionSnapshot,
    CancelResult,
)
from ..errors import UnsupportedOperation


class CodexDesktopRuntime(AgentRuntimeAdapter):
    runtime = "codex-desktop"
    capabilities = AgentCapabilities(
        runtime="codex-desktop",
        control_kind=ControlKind.GUI,
        supports_new_session=False,
        supports_session_reuse=False,
        supports_resume=False,
        supports_streaming=False,
        supports_structured_result=False,
        supports_structured_failure=False,
        supports_native_cancel=False,
        supports_usage=False,
        supports_permission_profiles=False,
        supports_workspace_isolation=False,
        supported_permission_profiles=(),
        max_concurrency=1,
        evidence_status=EvidenceStatus.PARTIAL,
        notes=(
            "Electron app; backend is a bundled codex app-server over stdio",
            "no documented external control API and no listening control socket",
            "shares ~/.codex sessions and queue_1.sqlite with Codex CLI",
            "codex:// appx protocol only launches/focuses the app",
            "GUI automation is fragile and intentionally not implemented",
            "not interchangeable with Codex CLI or Codex-Relay",
        ),
    )

    def __init__(self, codex_home: str | None = None) -> None:
        self.codex_home = Path(codex_home) if codex_home else Path.home() / ".codex"

    async def probe(self) -> dict[str, Any]:
        return {
            "runtime": self.runtime,
            "package_version": self._package_version(),
            "codex_home": str(self.codex_home),
            "shared_session_store": (self.codex_home / "sessions").exists(),
            "shared_queue_store": (self.codex_home / "queue_1.sqlite").exists(),
            "capabilities": self.capabilities.to_dict(),
        }

    def _package_version(self) -> str | None:
        try:
            powershell = str(
                Path(os.environ.get("SystemRoot", r"C:\Windows"))
                / "System32/WindowsPowerShell/v1.0/powershell.exe"
            )
            out = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-Command",
                    "(Get-AppxPackage -Name OpenAI.Codex | Select-Object -First 1).Version.ToString()",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return (out.stdout or "").strip() or None
        except (OSError, subprocess.SubprocessError):
            return None

    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        raise UnsupportedOperation(
            "codex-desktop has no external programmatic control interface; "
            "use Codex CLI or Codex-Relay instead"
        )

    async def inspect(self, execution_id: str) -> ExecutionSnapshot:
        raise UnsupportedOperation("codex-desktop does not expose execution state")

    async def cancel(self, execution_id: str) -> CancelResult:
        raise UnsupportedOperation("codex-desktop does not expose cancellation")
