"""Deterministic LlmExtractionPort for B2 tests and isolated demos."""

from __future__ import annotations

from collections.abc import Iterable

from .errors import ContextFlowError


class FakeExtractionAdapter:
    def __init__(self, responses: Iterable[object]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, tuple[str, ...] | None]] = []

    async def extract(
        self,
        text: str,
        known_agents: tuple[str, ...] | None = None,
    ) -> object:
        self.calls.append((text, known_agents))
        if not self._responses:
            raise ContextFlowError(
                "FAKE_PROVIDER_EXHAUSTED",
                "Fake extraction adapter has no configured response",
            )
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response
