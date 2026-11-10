"""Minimal configuration-backed authority for stable Agent identities."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import unicodedata
from typing import Any

from .contracts import AgentDescriptor, AgentDirectory, AgentId
from .errors import ContextFlowError
from .submission import MAX_ID_LENGTH


@dataclass(frozen=True)
class AgentCandidate:
    agent_id: AgentId
    name: str
    aliases: tuple[str, ...]
    available: bool


class ConfiguredAgentDirectory:
    def __init__(self, candidates: tuple[AgentCandidate, ...]) -> None:
        self._candidates = tuple(candidates)
        self._by_id = {item.agent_id: item for item in candidates}
        self._by_selector: dict[str, AgentCandidate] = {}
        for candidate in candidates:
            for selector in (candidate.name, *candidate.aliases):
                normalized = normalize_agent_selector(selector)
                if normalized in self._by_selector:
                    raise _invalid("Agent names and aliases must be globally unique")
                self._by_selector[normalized] = candidate

    @classmethod
    def from_file(cls, path: str | Path) -> "ConfiguredAgentDirectory":
        try:
            text = Path(path).expanduser().read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ContextFlowError(
                "AGENT_DIRECTORY_OPEN_FAILED",
                "cannot read Agent Directory configuration",
                details={"exception_type": type(exc).__name__},
            ) from exc
        try:
            raw = json.loads(
                text,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            raise _invalid("configuration must be strict UTF-8 JSON") from exc
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: object) -> "ConfiguredAgentDirectory":
        try:
            if not isinstance(raw, dict) or set(raw) != {"schema_version", "agents"}:
                raise ValueError("top-level fields")
            if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
                raise ValueError("schema_version")
            agents = raw["agents"]
            if not isinstance(agents, list):
                raise ValueError("agents")
            candidates: list[AgentCandidate] = []
            seen_ids: set[AgentId] = set()
            seen_selectors: set[str] = set()
            for item in agents:
                if not isinstance(item, dict) or set(item) != {
                    "agent_id", "name", "aliases", "available"
                }:
                    raise ValueError("Agent fields")
                agent_id_text = _configured_text(item["agent_id"], "agent_id")
                agent_id = AgentId(agent_id_text)
                if agent_id in seen_ids:
                    raise ValueError("duplicate Agent ID")
                seen_ids.add(agent_id)
                name = _selector(item["name"], "name")
                aliases_raw = item["aliases"]
                if not isinstance(aliases_raw, list):
                    raise ValueError("aliases")
                aliases = tuple(_selector(value, "alias") for value in aliases_raw)
                if type(item["available"]) is not bool:
                    raise ValueError("available")
                for value in (name, *aliases):
                    normalized = normalize_agent_selector(value)
                    if normalized in seen_selectors:
                        raise ValueError("duplicate selector")
                    seen_selectors.add(normalized)
                candidates.append(
                    AgentCandidate(
                        agent_id=agent_id,
                        name=name,
                        aliases=aliases,
                        available=item["available"],
                    )
                )
            return cls(tuple(candidates))
        except (ContextFlowError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
            if isinstance(exc, ContextFlowError) and exc.code == "AGENT_DIRECTORY_INVALID":
                raise
            raise _invalid("configuration violates the Agent Directory schema") from exc

    def candidates(self) -> tuple[AgentCandidate, ...]:
        return self._candidates

    async def known_agents(self) -> tuple[str, ...]:
        return tuple(item.name for item in self._candidates)

    async def resolve(self, target: str) -> AgentDescriptor | None:
        # AgentId is a str subclass but is not a target selector at this boundary.
        if type(target) is not str:
            raise _argument("target", "must be a target string")
        try:
            normalized = normalize_agent_selector(target)
        except (TypeError, ValueError, UnicodeError) as exc:
            raise _argument("target", "must be a non-empty valid Unicode string") from exc
        candidate = self._by_selector.get(normalized)
        return _descriptor(candidate) if candidate is not None else None

    async def get_agent(self, agent_id: AgentId) -> AgentDescriptor | None:
        if not isinstance(agent_id, AgentId):
            raise _argument("agent_id", "must be an AgentId")
        candidate = self._by_id.get(agent_id)
        return _descriptor(candidate) if candidate is not None else None


def normalize_agent_selector(value: str) -> str:
    if type(value) is not str:
        raise ValueError("selector must be plain text")
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    if not normalized:
        raise ValueError("selector must not be blank")
    return normalized


def _configured_text(value: object, field: str) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > MAX_ID_LENGTH:
        raise ValueError(f"{field} must be exact non-empty text")
    value.encode("utf-8")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValueError(f"{field} cannot contain control characters")
    return value


def _selector(value: object, field: str) -> str:
    value = _configured_text(value, field)
    normalize_agent_selector(value)
    return value


def _descriptor(candidate: AgentCandidate) -> AgentDescriptor:
    return AgentDescriptor(
        agent_id=candidate.agent_id,
        available=candidate.available,
    )


def _argument(field: str, reason: str) -> ContextFlowError:
    return ContextFlowError(
        "INVALID_ARGUMENT",
        f"invalid {field}: {reason}",
        details={"field": field},
    )


def _invalid(reason: str) -> ContextFlowError:
    return ContextFlowError(
        "AGENT_DIRECTORY_INVALID",
        "Agent Directory configuration is invalid",
        details={"reason": reason},
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")
