"""Provider-backed instruction extraction with trusted target validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import unicodedata

from .agent_directory import AgentCandidate, ConfiguredAgentDirectory
from .contracts import (
    ErrorInfo,
    LlmExtractionPort,
    ParseResult,
    ParseStatus,
    ParsedInstruction,
)
from .errors import ContextFlowError


@dataclass(frozen=True)
class _RawInstruction:
    candidate: AgentCandidate
    content: str
    excerpt: str
    source_position: int


class LlmInstructionParser:
    def __init__(
        self,
        extraction: LlmExtractionPort,
        directory: ConfiguredAgentDirectory,
    ) -> None:
        if not isinstance(extraction, LlmExtractionPort):
            raise _argument("extraction", "must implement LlmExtractionPort")
        if not isinstance(directory, ConfiguredAgentDirectory):
            raise _argument("directory", "must be a ConfiguredAgentDirectory")
        self.extraction = extraction
        self.directory = directory

    def trusted_selectors(self) -> tuple[str, ...]:
        return tuple(
            selector
            for candidate in self.directory.candidates()
            for selector in (candidate.name, *candidate.aliases)
        )

    async def parse(
        self,
        text: str,
        known_agents: tuple[str, ...] | None = None,
    ) -> ParseResult:
        if type(text) is not str or not text.strip():
            raise _argument("text", "must be a non-empty string")
        try:
            text.encode("utf-8")
        except UnicodeError as exc:
            raise _argument("text", "must be valid UTF-8 Unicode") from exc

        candidates = self._candidate_subset(known_agents)
        if not candidates:
            return ParseResult(
                status=ParseStatus.UNRESOLVED,
                error=ErrorInfo(
                    code="NO_AGENT_CANDIDATES",
                    message="no trusted Agent candidates are configured",
                ),
            )
        prompt_selectors = tuple(
            selector
            for candidate in candidates
            for selector in (candidate.name, *candidate.aliases)
        )
        raw = await self.extraction.extract(text, prompt_selectors)
        status, instructions, reason = _validate_shape(raw)
        if status == "unresolved":
            return ParseResult(
                status=ParseStatus.UNRESOLVED,
                error=ErrorInfo(code="INTENT_UNRESOLVED", message=reason),
            )

        allowed = {candidate.agent_id: candidate for candidate in candidates}
        resolved: list[_RawInstruction] = []
        cursor = 0
        for item in instructions:
            target = await self.directory.resolve(item["target_agent"])
            if target is None or target.agent_id not in allowed:
                return ParseResult(
                    status=ParseStatus.UNRESOLVED,
                    error=ErrorInfo(
                        code="TARGET_UNRESOLVED",
                        message="Parser selected a target outside the trusted candidate set",
                    ),
                )
            candidate = allowed[target.agent_id]
            excerpt = item["source_excerpt"]
            position = text.find(excerpt, cursor)
            if position < 0:
                raise _invalid_output(
                    "source excerpts must occur in the original message and preserve order"
                )
            cursor = position + len(excerpt)
            resolved.append(
                _RawInstruction(
                    candidate=candidate,
                    content=item["content"],
                    excerpt=excerpt,
                    source_position=position,
                )
            )

        selected_ids = {item.candidate.agent_id for item in resolved}
        mentioned_ids = _explicitly_mentioned_candidates(text, candidates)
        if not mentioned_ids:
            raise _invalid_output(
                "parsed output requires at least one trusted Agent selector in the source message"
            )
        if selected_ids != mentioned_ids:
            if not mentioned_ids.issubset(selected_ids):
                raise ContextFlowError(
                    "LLM_OUTPUT_INCOMPLETE",
                    "Parser output omitted an explicitly mentioned trusted Agent target",
                    retryable=True,
                    details={"omitted_target_count": len(mentioned_ids - selected_ids)},
                )
            raise _invalid_output(
                "parsed output added a trusted Agent target not explicitly named in the source"
            )

        return ParseResult(
            status=ParseStatus.PARSED,
            instructions=tuple(
                ParsedInstruction(
                    target_agent=item.candidate.name,
                    content=item.content,
                )
                for item in resolved
            ),
        )

    def _candidate_subset(
        self, known_agents: tuple[str, ...] | None
    ) -> tuple[AgentCandidate, ...]:
        all_candidates = self.directory.candidates()
        if known_agents is None:
            return all_candidates
        if not isinstance(known_agents, tuple) or any(type(item) is not str for item in known_agents):
            raise _argument("known_agents", "must be a tuple of trusted selector strings or None")
        selected: list[AgentCandidate] = []
        selected_ids = set()
        selector_map = {
            selector.casefold(): candidate
            for candidate in all_candidates
            for selector in (candidate.name, *candidate.aliases)
        }
        for selector in known_agents:
            candidate = selector_map.get(selector.strip().casefold())
            if candidate is None:
                raise _argument("known_agents", "contains a selector outside the trusted directory")
            if candidate.agent_id not in selected_ids:
                selected.append(candidate)
                selected_ids.add(candidate.agent_id)
        return tuple(selected)


def _validate_shape(raw: object) -> tuple[str, list[dict[str, str]], str | None]:
    if not isinstance(raw, dict) or set(raw) != {"status", "instructions", "reason"}:
        raise _invalid_output("top-level fields must be exactly status, instructions and reason")
    status = raw["status"]
    instructions = raw["instructions"]
    reason = raw["reason"]
    if status not in ("parsed", "unresolved") or not isinstance(instructions, list):
        raise _invalid_output("status or instructions type is invalid")
    if status == "parsed":
        if not instructions or reason is not None:
            raise _invalid_output("parsed requires instructions and null reason")
    elif instructions or type(reason) is not str or not reason.strip():
        raise _invalid_output("unresolved requires no instructions and a non-empty reason")

    for item in instructions:
        if not isinstance(item, dict) or set(item) != {
            "target_agent", "content", "source_excerpt"
        }:
            raise _invalid_output("instruction fields are invalid")
        for field in ("target_agent", "content", "source_excerpt"):
            value = item[field]
            if type(value) is not str or not value.strip():
                raise _invalid_output(f"instruction {field} must be non-empty text")
            try:
                value.encode("utf-8")
            except UnicodeError as exc:
                raise _invalid_output(f"instruction {field} must be valid Unicode") from exc
    return status, instructions, reason


def _ascii_identifier(character: str) -> bool:
    return bool(character) and character.isascii() and (
        character.isalnum() or character == "_"
    )


def _selector_occurs(text: str, selector: str) -> bool:
    folded_text = unicodedata.normalize("NFKC", text).casefold()
    folded_selector = unicodedata.normalize("NFKC", selector).casefold()
    start = 0
    while True:
        index = folded_text.find(folded_selector, start)
        if index < 0:
            return False
        end = index + len(folded_selector)
        before = folded_text[index - 1] if index > 0 else ""
        after = folded_text[end] if end < len(folded_text) else ""
        if not _ascii_identifier(before) and not _ascii_identifier(after):
            return True
        start = index + 1


def _explicitly_mentioned_candidates(
    text: str, candidates: tuple[AgentCandidate, ...]
) -> set:
    return {
        candidate.agent_id
        for candidate in candidates
        if any(_selector_occurs(text, selector) for selector in (candidate.name, *candidate.aliases))
    }


def _argument(field: str, reason: str) -> ContextFlowError:
    return ContextFlowError(
        "INVALID_ARGUMENT",
        f"invalid {field}: {reason}",
        details={"field": field},
    )


def _invalid_output(reason: str) -> ContextFlowError:
    return ContextFlowError(
        "LLM_OUTPUT_INVALID",
        "LLM output does not satisfy the Parser schema",
        retryable=True,
        details={"reason": reason},
    )
