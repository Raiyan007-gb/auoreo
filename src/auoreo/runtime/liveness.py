"""Turn liveness watchdog (spec §8.8): `turn_liveness_s` (600 s) timeout, 15 s poll.

One watchdog per running turn, bound to the session runtime's turn *generation*. Stream
items, tool starts/ends and tool progress events `touch()` it; a pending approval or
question counts as progress (a human is deciding). With no progress for the window it
emits `turn.liveness_timeout` and cancels the turn task, which then ends `aborted` with
code `LIVENESS`. A watchdog whose generation is no longer current (an older turn) exits
without doing anything.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from auoreo.core.services import Services
    from auoreo.runtime.runner import SessionRuntime


class LivenessWatchdog:
    def __init__(self, services: Services, rt: SessionRuntime, run_id: str) -> None:
        cfg = services.settings.loop
        self.s = services
        self.rt = rt
        self.run_id = run_id
        self.generation = rt.generation
        self.timeout = float(cfg.turn_liveness_s)
        self.poll = max(0.005, min(float(cfg.turn_liveness_poll_s), self.timeout / 4))
        self.last = time.monotonic()
        self.fired = False
        self._task: asyncio.Task[None] | None = None

    def touch(self, *_: Any) -> None:
        self.last = time.monotonic()

    def start(self, turn: asyncio.Task[Any]) -> None:
        self._task = asyncio.create_task(self._watch(turn), name=f"liveness:{self.rt.session_id}")

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def _waiting_on_human(self) -> bool:
        sid = self.rt.session_id
        return bool(self.s.approvals.pending(sid) or self.s.questions.pending(sid))

    async def _watch(self, turn: asyncio.Task[Any]) -> None:
        while not turn.done():
            await asyncio.sleep(self.poll)
            if self.rt.generation != self.generation:
                return  # stale: a newer turn owns the runtime
            if self._waiting_on_human():
                self.touch()
            idle = time.monotonic() - self.last
            if idle < self.timeout or turn.done():
                continue
            self.fired = True
            await self.s.emit(
                self.rt.session_id,
                "turn.liveness_timeout",
                {"run_id": self.run_id, "idle_s": round(idle, 3), "timeout_s": self.timeout},
            )
            turn.cancel()
            return
