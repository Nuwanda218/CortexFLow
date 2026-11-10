"""Deterministic InstructionParser used by B1 tests and isolated demos."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Iterable

from .errors import ContextFlowError


@dataclass(frozen=True)
class BlockingStep:
    result: object
    started: asyncio.Event
    release: asyncio.Event
    ignore_cancellation: bool = False


class FakeInstructionParser:
    """Consume a finite script of values, exceptions, or controlled blocks."""

    def __init__(self, steps: Iterable[object]) -> None:
        self._steps = list(steps)
        self.calls: list[tuple[str, tuple[str, ...] | None]] = []

    async def parse(
        self,
        text: str,
        known_agents: tuple[str, ...] | None = None,
    ) -> Any:
        self.calls.append((text, known_agents))
        if not self._steps:
            raise ContextFlowError(
                "FAKE_SCRIPT_EXHAUSTED",
                "Fake Parser has no configured response",
            )
        step = self._steps.pop(0)
        if isinstance(step, BlockingStep):
            step.started.set()
            if step.ignore_cancellation:
                while not step.release.is_set():
                    try:
                        await step.release.wait()
                    except asyncio.CancelledError:
                        continue
            else:
                await step.release.wait()
            step = step.result
        if isinstance(step, BaseException):
            raise step
        return step
