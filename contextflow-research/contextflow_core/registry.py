"""Persistent endpoint identity registry."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ContextFlowError
from .models import EndpointBinding


SCHEMA_VERSION = 1


def default_registry_path() -> Path:
    configured = os.environ.get("CONTEXTFLOW_ENDPOINT_REGISTRY")
    if configured:
        return Path(configured).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "ContextFlow" / "endpoints.json"
    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        return Path(state_home) / "contextflow" / "endpoints.json"
    return Path.home() / ".local" / "state" / "contextflow" / "endpoints.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class EndpointRegistry:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_registry_path()
        self._by_id: dict[str, EndpointBinding] = {}
        self._by_native: dict[tuple[str, str], EndpointBinding] = {}
        self._load()

    def get(self, endpoint_id: str) -> EndpointBinding | None:
        return self._by_id.get(endpoint_id)

    def find(self, provider: str, native_session_id: str) -> EndpointBinding | None:
        return self._by_native.get((provider, native_session_id))

    def all(self) -> list[EndpointBinding]:
        return list(self._by_id.values())

    def register(
        self,
        provider: str,
        native_session_id: str,
        session_kind: str | None = None,
    ) -> tuple[EndpointBinding, bool]:
        if not provider or not native_session_id:
            raise ContextFlowError(
                "INVALID_ARGUMENT",
                "provider and native_session_id must be non-empty",
            )
        existing = self.find(provider, native_session_id)
        if existing is not None:
            return existing, False

        now = _now_iso()
        binding = EndpointBinding(
            endpoint_id=f"cfep_{uuid.uuid4().hex}",
            provider=provider,
            native_session_id=native_session_id,
            session_kind=session_kind,
            created_at=now,
            updated_at=now,
        )
        self._by_id[binding.endpoint_id] = binding
        self._by_native[(binding.provider, binding.native_session_id)] = binding
        try:
            self._save()
        except Exception:
            self._by_id.pop(binding.endpoint_id, None)
            self._by_native.pop((binding.provider, binding.native_session_id), None)
            raise
        return binding, True

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("unsupported or missing schema_version")
            records = document.get("endpoints")
            if not isinstance(records, list):
                raise ValueError("endpoints must be a list")
            for raw in records:
                if not isinstance(raw, dict):
                    raise ValueError("endpoint record must be an object")
                binding = EndpointBinding.from_dict(raw)
                native_key = (binding.provider, binding.native_session_id)
                if not binding.endpoint_id or not all(native_key):
                    raise ValueError("endpoint identity fields must be non-empty")
                if binding.endpoint_id in self._by_id or native_key in self._by_native:
                    raise ValueError("duplicate endpoint identity")
                self._by_id[binding.endpoint_id] = binding
                self._by_native[native_key] = binding
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self._by_id.clear()
            self._by_native.clear()
            raise ContextFlowError(
                "REGISTRY_CORRUPT",
                f"cannot load endpoint registry {self.path}: {exc}",
                details={"path": str(self.path)},
            ) from exc

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "endpoints": [binding.to_dict() for binding in self.all()],
        }
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                json.dump(document, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        except OSError as exc:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise ContextFlowError(
                "REGISTRY_WRITE_FAILED",
                f"cannot persist endpoint registry {self.path}: {exc}",
                retryable=True,
                details={"path": str(self.path)},
            ) from exc
