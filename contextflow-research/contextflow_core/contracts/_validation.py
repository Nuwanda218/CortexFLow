"""Small, side-effect-free validation helpers shared by contract records."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
import math
from types import MappingProxyType
from typing import Any

from ..errors import ContextFlowError


def invalid(field: str, reason: str) -> ContextFlowError:
    return ContextFlowError(
        "INVALID_CONTRACT",
        f"invalid contract field {field}: {reason}",
        details={"field": field, "reason": reason},
    )


def require_type(value: object, expected: type | tuple[type, ...], field: str) -> None:
    if not isinstance(value, expected):
        raise invalid(field, "wrong value type")


def require_text(value: object, field: str, max_length: int | None = None) -> None:
    if not isinstance(value, str) or not value.strip():
        raise invalid(field, "must be a non-empty string")
    if max_length is not None and len(value) > max_length:
        raise invalid(field, f"must be at most {max_length} characters")


def require_tuple(value: object, item_type: type | tuple[type, ...], field: str) -> None:
    require_type(value, tuple, field)
    for item in value:
        require_type(item, item_type, field)


def require_utc(value: object, field: str) -> None:
    require_text(value, field)
    try:
        stamp = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise invalid(field, "must be an ISO timestamp with UTC timezone") from exc
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise invalid(field, "must be an ISO timestamp with UTC timezone")


def exact_fields(value: object, fields: tuple[str, ...], field: str) -> dict[str, Any]:
    require_type(value, dict, field)
    if set(value) != set(fields):
        raise invalid(field, "missing or unexpected fields")
    return value


MAX_JSON_DEPTH = 64  # Container nesting; the top-level mapping is depth zero.


def freeze_json(value: Any, field: str) -> Any:
    ancestors: set[int] = set()

    def visit(item: Any, depth: int) -> Any:
        if item is None or isinstance(item, (bool, int)):
            return item
        if isinstance(item, str):
            return str.__str__(item)
        if isinstance(item, float):
            if not math.isfinite(item):
                raise invalid(field, "non-finite numbers are not JSON values")
            return item
        if not isinstance(item, (Mapping, list, tuple)):
            raise invalid(field, "must contain only JSON values")
        if depth > MAX_JSON_DEPTH:
            raise invalid(field, f"JSON nesting exceeds depth {MAX_JSON_DEPTH}")
        identity = id(item)
        if identity in ancestors:
            raise invalid(field, "cyclic values are not JSON")
        ancestors.add(identity)
        try:
            if isinstance(item, Mapping):
                if not all(isinstance(key, str) for key in item):
                    raise invalid(field, "JSON object keys must be strings")
                return MappingProxyType({
                    str.__str__(key): visit(child, depth + 1)
                    for key, child in item.items()
                })
            return tuple(visit(child, depth + 1) for child in item)
        finally:
            ancestors.remove(identity)

    return visit(value, 0)


def freeze_mapping(value: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    require_type(value, Mapping, field)
    return freeze_json(value, field)


def thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value
