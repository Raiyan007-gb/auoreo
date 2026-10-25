"""Session inbox (spec §8.1): followup → next turn + wake; steer → next step + wake;
inject → next step, no wake."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

InputKind = Literal["followup", "steer", "inject"]


@dataclass(frozen=True)
class RunOptions:
    """Per-run overrides that automation, jobs and delegation apply to a turn."""

    max_steps: int | None = None
    tools_allow: tuple[str, ...] | None = None  # fnmatch globs; None = agent default
    tools_deny: tuple[str, ...] = ()
    skip_memory: bool = False


@dataclass
class TurnRequest:
    run_id: str
    text: str
    agent: str | None = None
    workflow: str | None = None
    model: str | None = None
    options: RunOptions | None = None


@dataclass
class Inbox:
    next_turn: deque[TurnRequest] = field(default_factory=deque)
    next_step: deque[str] = field(default_factory=deque)
    wake: asyncio.Event = field(default_factory=asyncio.Event)

    def followup(self, req: TurnRequest) -> None:
        self.next_turn.append(req)
        self.wake.set()

    def steer(self, text: str) -> None:
        self.next_step.append(text)
        self.wake.set()

    def inject(self, text: str) -> None:
        self.next_step.append(text)

    def claim_step(self) -> list[str]:
        """Drain all next-step input (steer/inject), newline-coalesced by the caller."""
        items = list(self.next_step)
        self.next_step.clear()
        return items

    def claim_turn(self) -> TurnRequest | None:
        return self.next_turn.popleft() if self.next_turn else None
