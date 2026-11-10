"""Strict, canonical persistence codec for the frozen stage-0 ParseResult."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from .contracts import ErrorInfo, ParseResult, ParseStatus, ParsedInstruction
from .errors import ContextFlowError


@dataclass(frozen=True)
class EncodedParseResult:
    status: str
    instructions_json: str
    error_json: str | None
    fingerprint: str | None


def _argument(reason: str) -> ContextFlowError:
    return ContextFlowError(
        "INVALID_ARGUMENT",
        f"invalid parse result: {reason}",
        details={"field": "result"},
    )


def _corrupt(reason: str) -> ContextFlowError:
    return ContextFlowError(
        "PARSE_RESULT_CORRUPT",
        "stored ParseResult violates its contract",
        details={"reason": reason},
    )


def _canonical(value: object) -> str:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        text.encode("utf-8")
        return text
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise _argument("must contain canonical UTF-8 JSON values") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _load(text: object, field: str) -> Any:
    if not isinstance(text, str):
        raise _corrupt(f"{field} must be JSON text")
    try:
        text.encode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise _corrupt(f"{field} is not strict UTF-8 JSON") from exc


def encode_parse_result(result: ParseResult) -> EncodedParseResult:
    if not isinstance(result, ParseResult):
        raise _argument("must be a ParseResult")

    instructions = [
        {"target_agent": item.target_agent, "content": item.content}
        for item in result.instructions
    ]
    error_json = _canonical(result.error.to_dict()) if result.error is not None else None
    return EncodedParseResult(
        status=result.status.value,
        instructions_json=_canonical(instructions),
        error_json=error_json,
        fingerprint=result.intent_fingerprint,
    )


def _error_from_json(text: str) -> ErrorInfo:
    raw = _load(text, "error_json")
    if not isinstance(raw, dict) or set(raw) != {
        "code", "message", "retryable", "details"
    }:
        raise _corrupt("error_json has missing or unexpected fields")
    if type(raw["retryable"]) is not bool or not isinstance(raw["details"], dict):
        raise _corrupt("error_json has invalid field types")
    try:
        return ErrorInfo(
            code=raw["code"],
            message=raw["message"],
            retryable=raw["retryable"],
            details=raw["details"],
        )
    except (ContextFlowError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise _corrupt("error_json violates ErrorInfo") from exc


def decode_parse_result(encoded: EncodedParseResult) -> ParseResult:
    if not isinstance(encoded, EncodedParseResult):
        raise _corrupt("encoded value has the wrong type")
    try:
        status = ParseStatus(encoded.status)
    except (TypeError, ValueError) as exc:
        raise _corrupt("unsupported result status") from exc

    raw_instructions = _load(encoded.instructions_json, "instructions_json")
    if not isinstance(raw_instructions, list):
        raise _corrupt("instructions_json must be an array")
    instructions: list[ParsedInstruction] = []
    try:
        for raw in raw_instructions:
            if not isinstance(raw, dict) or set(raw) != {"target_agent", "content"}:
                raise _corrupt("instruction has missing or unexpected fields")
            instructions.append(
                ParsedInstruction(
                    target_agent=raw["target_agent"],
                    content=raw["content"],
                )
            )
    except (ContextFlowError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, ContextFlowError) and exc.code == "PARSE_RESULT_CORRUPT":
            raise
        raise _corrupt("instruction violates ParsedInstruction") from exc

    error = None
    if encoded.error_json is not None:
        error = _error_from_json(encoded.error_json)
    try:
        result = ParseResult(
            status=status,
            instructions=tuple(instructions),
            error=error,
        )
    except (ContextFlowError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise _corrupt("status, instructions and error are inconsistent") from exc

    fingerprint = encoded.fingerprint
    if result.intent_fingerprint != fingerprint:
        raise _corrupt("intent fingerprint does not match the ordered instructions")
    if fingerprint is not None and (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in fingerprint)
    ):
        raise _corrupt("intent fingerprint is not lowercase SHA-256")
    return result
