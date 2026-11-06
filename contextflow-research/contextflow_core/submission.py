"""CF-ADAPTER-001: message submission envelope and test receiver.

This module is deliberately transport-agnostic.  It defines:

* the standard Assistant-message envelope produced by the ChatGPT Adapter;
* strict validation for that envelope;
* :class:`MockCoreReceiver`, a lightweight, file-backed receiver used by the
  Python test scripts to verify acceptance, idempotency and conflict handling.

It is *not* the ContextFlow Core.  It has no queue, no parser, no router and no
task state machine; it only records what the Adapter submitted and returns a
short acknowledgement.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ContextFlowError

SCHEMA_VERSION = 1
ADAPTER_NAME = "chatgpt_web"
CONTENT_FORMAT = "markdown"

SUBMISSION_ID_RE = re.compile(r"^sub_[A-Za-z0-9_-]{8,}$")
ENDPOINT_ID_RE = re.compile(r"^cfep_[A-Za-z0-9_-]{8,}$")

TOP_LEVEL_FIELDS = ("schema_version", "submission_id", "source", "content")
SOURCE_FIELDS = (
    "adapter",
    "endpoint_id",
    "native_session_id",
    "message_id",
    "user_message_id",
    "turn_group_id",
)
CONTENT_FIELDS = ("format", "text")

MAX_TEXT_LENGTH = 2_000_000
MAX_ID_LENGTH = 256


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _invalid(field: str, reason: str, **details: Any) -> ContextFlowError:
    payload = {"field": field, "reason": reason}
    payload.update(details)
    return ContextFlowError(
        "INVALID_ENVELOPE",
        f"invalid envelope field {field}: {reason}",
        details=payload,
    )


def _require_exact_fields(value: dict[str, Any], expected: tuple[str, ...], where: str) -> None:
    missing = [name for name in expected if name not in value]
    if missing:
        raise _invalid(where, "missing required fields", missing=missing)
    unexpected = [name for name in value if name not in expected]
    if unexpected:
        raise _invalid(where, "unexpected fields", unexpected=unexpected)


def _require_text(value: Any, field: str, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _invalid(field, "must be a non-empty string")
    if len(value) > MAX_ID_LENGTH:
        raise _invalid(field, f"must be at most {MAX_ID_LENGTH} characters")
    return value


def validate_envelope(envelope: Any) -> dict[str, Any]:
    """Validate and normalize one submission envelope.

    Returns a new dict containing exactly the documented fields.  Raises
    :class:`ContextFlowError` with code ``INVALID_ENVELOPE`` on any violation.
    The Adapter must not send an envelope that fails this validation, and the
    receiver must reject it instead of storing it.
    """

    if not isinstance(envelope, dict):
        raise _invalid("$", "envelope must be a JSON object")
    _require_exact_fields(envelope, TOP_LEVEL_FIELDS, "$")

    schema_version = envelope["schema_version"]
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise _invalid("schema_version", "must be an integer")
    if schema_version != SCHEMA_VERSION:
        raise _invalid("schema_version", f"unsupported version {schema_version}")

    submission_id = envelope["submission_id"]
    if not isinstance(submission_id, str) or not SUBMISSION_ID_RE.match(submission_id):
        raise _invalid("submission_id", "must match sub_<id>")

    source = envelope["source"]
    if not isinstance(source, dict):
        raise _invalid("source", "must be an object")
    _require_exact_fields(source, SOURCE_FIELDS, "source")

    adapter = source["adapter"]
    if adapter != ADAPTER_NAME:
        raise _invalid("source.adapter", f"must be {ADAPTER_NAME!r}")

    endpoint_id = source["endpoint_id"]
    if not isinstance(endpoint_id, str) or not ENDPOINT_ID_RE.match(endpoint_id):
        raise _invalid("source.endpoint_id", "must match cfep_<id>")

    native_session_id = _require_text(source["native_session_id"], "source.native_session_id")
    message_id = _require_text(source["message_id"], "source.message_id")
    user_message_id = _require_text(
        source["user_message_id"], "source.user_message_id", allow_none=True
    )
    turn_group_id = _require_text(
        source["turn_group_id"], "source.turn_group_id", allow_none=True
    )

    content = envelope["content"]
    if not isinstance(content, dict):
        raise _invalid("content", "must be an object")
    _require_exact_fields(content, CONTENT_FIELDS, "content")

    if content["format"] != CONTENT_FORMAT:
        raise _invalid("content.format", f"must be {CONTENT_FORMAT!r}")
    text = content["text"]
    if not isinstance(text, str) or not text.strip():
        raise _invalid("content.text", "must be a non-empty string")
    if len(text) > MAX_TEXT_LENGTH:
        raise _invalid("content.text", f"must be at most {MAX_TEXT_LENGTH} characters")

    return {
        "schema_version": SCHEMA_VERSION,
        "submission_id": submission_id,
        "source": {
            "adapter": ADAPTER_NAME,
            "endpoint_id": endpoint_id,
            "native_session_id": native_session_id,
            "message_id": message_id,
            "user_message_id": user_message_id,
            "turn_group_id": turn_group_id,
        },
        "content": {"format": CONTENT_FORMAT, "text": text},
    }


def canonical_envelope(envelope: dict[str, Any]) -> str:
    """Stable serialization used for duplicate/conflict comparison."""

    return json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def envelope_fingerprint(envelope: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_envelope(envelope).encode("utf-8")).hexdigest()


def accepted_ack(submission_id: str, *, duplicate: bool = False) -> dict[str, Any]:
    return {
        "submission_id": submission_id,
        "status": "accepted",
        "duplicate": duplicate,
    }


def rejected_ack(
    submission_id: str | None,
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return {
        "submission_id": submission_id,
        "status": "rejected",
        "error": error,
    }


def conflict_ack(submission_id: str, stored_fingerprint: str, incoming_fingerprint: str) -> dict[str, Any]:
    return {
        "submission_id": submission_id,
        "status": "conflict",
        "error": {
            "code": "SUBMISSION_CONFLICT",
            "message": "submission_id already accepted with different content",
            "details": {
                "stored_fingerprint": stored_fingerprint,
                "incoming_fingerprint": incoming_fingerprint,
            },
        },
    }


class MockCoreReceiver:
    """File-backed, idempotent receiver used by the CF-ADAPTER-001 test scripts.

    Semantics:

    * a valid envelope is stored once per ``submission_id``;
    * the same envelope submitted again returns the same ``accepted`` ack with
      ``duplicate=true`` and does not create a second record;
    * the same ``submission_id`` with different content returns ``conflict``
      and never overwrites the stored record;
    * an invalid envelope is rejected and never stored.
    """

    def __init__(self, store_path: str | Path | None = None) -> None:
        self.path = Path(store_path) if store_path is not None else None
        self._records: dict[str, dict[str, Any]] = {}
        self._load()

    # -- persistence -----------------------------------------------------
    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("unsupported or missing schema_version")
            records = document.get("submissions")
            if not isinstance(records, dict):
                raise ValueError("submissions must be an object")
            for submission_id, record in records.items():
                if not isinstance(record, dict) or not isinstance(record.get("envelope"), dict):
                    raise ValueError("submission record must contain an envelope")
                self._records[submission_id] = record
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self._records.clear()
            raise ContextFlowError(
                "MOCK_STORE_CORRUPT",
                f"cannot load mock submission store {self.path}: {exc}",
                details={"path": str(self.path)},
            ) from exc

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "schema_version": SCHEMA_VERSION,
            "updated_at": _now_iso(),
            "submissions": self._records,
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
                "MOCK_STORE_WRITE_FAILED",
                f"cannot persist mock submission store {self.path}: {exc}",
                retryable=True,
                details={"path": str(self.path)},
            ) from exc

    # -- receive ---------------------------------------------------------
    def receive(self, envelope: Any) -> dict[str, Any]:
        raw_submission_id = envelope.get("submission_id") if isinstance(envelope, dict) else None
        try:
            normalized = validate_envelope(envelope)
        except ContextFlowError as exc:
            return rejected_ack(
                raw_submission_id if isinstance(raw_submission_id, str) else None,
                exc.code,
                exc.message,
                details=exc.details,
            )

        submission_id = normalized["submission_id"]
        fingerprint = envelope_fingerprint(normalized)
        existing = self._records.get(submission_id)
        if existing is not None:
            if existing.get("fingerprint") == fingerprint:
                return accepted_ack(submission_id, duplicate=True)
            return conflict_ack(
                submission_id,
                str(existing.get("fingerprint")),
                fingerprint,
            )

        ack = accepted_ack(submission_id, duplicate=False)
        self._records[submission_id] = {
            "submission_id": submission_id,
            "fingerprint": fingerprint,
            "envelope": normalized,
            "ack": ack,
            "received_at": _now_iso(),
        }
        self._save()
        return ack

    # -- inspection ------------------------------------------------------
    def get(self, submission_id: str) -> dict[str, Any] | None:
        return self._records.get(submission_id)

    def all(self) -> list[dict[str, Any]]:
        return list(self._records.values())

    def count(self) -> int:
        return len(self._records)

    def reset(self) -> None:
        self._records.clear()
        self._save()
