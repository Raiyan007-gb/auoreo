"""Session inbox (spec §8.1): followup → next turn + wake; steer → next step + wake;
inject → next step, no wake."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

InputKind = Literal["followup", "steer", "inject"]


class InboxFull(Exception):
    """`queue_bound` queued followups already wait; the API maps this to 429 + Retry-After."""

    def __init__(self, session_id: str, bound: int, retry_after_s: int = 2) -> None:
        super().__init__(f"session {session_id} has {bound} queued messages; retry after {retry_after_s}s")
        self.session_id = session_id
        self.bound = bound
        self.retry_after_s = retry_after_s


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

    def full(self, bound: int | None) -> bool:
        return bound is not None and len(self.next_turn) >= bound

    def edit(self, run_id: str, text: str) -> bool:
        """Replace the text of a queued (not yet started) followup; False once it has run."""
        for req in self.next_turn:
            if req.run_id == run_id:
                req.text = text
                return True
        return False

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
