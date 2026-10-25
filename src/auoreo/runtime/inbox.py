"""Session inbox (spec §8.1): followup → next turn + wake; steer → next step + wake;
inject → next step, no wake. `claim_turn` also drains next-step input; `cancel` keeps
pending input for the next turn unless told otherwise."""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Literal

OUT_OF_BAND = "[OUT-OF-BAND]"  # == runtime.budget.OUT_OF_BAND (kept import-free here)

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
        self.next_step.append(f"{OUT_OF_BAND} {text}")  # harness/peer input, not the user

    def claim_step(self) -> list[str]:
        """Drain all next-step input (steer/inject), newline-coalesced by the caller."""
        items = list(self.next_step)
        self.next_step.clear()
        return items

    def claim_turn(self) -> TurnRequest | None:
        """One next-turn request; pending next-step input is drained into its batch (§8.1)."""
        if not self.next_turn:
            return None
        req = self.next_turn.popleft()
        steps = self.claim_step()
        return replace(req, text="\n\n".join([req.text, *steps])) if steps else req

    def cancel(self, cause: str, keep_inbox: bool = True) -> None:
        """Turn cancelled (`cause`: user | liveness | shutdown …). With `keep_inbox` pending
        next-step input becomes the first next-turn request (so it is not lost) and queued
        followups stay; otherwise both lists are dropped."""
        steps = self.claim_step()
        if not keep_inbox:
            self.next_turn.clear()
            return
        if steps:
            run_id = f"run_{uuid.uuid4().hex[:12]}"
            self.next_turn.appendleft(TurnRequest(run_id, "\n\n".join(steps)))
