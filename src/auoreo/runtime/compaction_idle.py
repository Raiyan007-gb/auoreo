"""S4(c) idle compaction trigger (spec §11.9).

`compaction.idle_compact_after_seconds` (default None = off): when a session's drain task
goes idle, a timer starts; if no input arrives before it fires and the history holds at
least `compaction.idle_min_tokens`, the session is compacted in the background
(`trigger="idle"`). New input cancels a pending timer; a compaction already summarizing is
cancelled by the foreground claim (background never blocks foreground), but one inside the
replace transaction is awaited so the history never changes under a running turn.
Each idle period compacts at most once (no repeat until a turn has run again).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from functools import partial
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from auoreo.core.services import Services
    from auoreo.runtime.compaction import CompactionService
    from auoreo.runtime.runner import SessionRuntime

log = logging.getLogger(__name__)


class IdleCompactor:
    def __init__(self, services: Services, compaction: CompactionService) -> None:
        self.s = services
        self.comp = compaction
        self._tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def after_s(self) -> float | None:
        return self.s.settings.compaction.idle_compact_after_seconds

    def schedule(self, rt: SessionRuntime) -> None:
        """Called when the session's drain ends (the session just went idle)."""
        self.cancel_timer(rt.session_id)
        if self.after_s is None or rt.subagent or not self.comp.enabled:
            return
        task = asyncio.create_task(self._fire(rt, self.after_s), name=f"idle-compact:{rt.session_id}")
        self._tasks[rt.session_id] = task
        task.add_done_callback(partial(self._done, rt.session_id))

    def _done(self, sid: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(sid) is task:
            del self._tasks[sid]

    def cancel_timer(self, sid: str) -> None:
        task = self._tasks.get(sid)
        if task is not None and not task.done():
            task.cancel()

    async def preempt(self, sid: str) -> None:
        """Foreground claim: cancel a pending/summarizing idle compaction, await a committing one."""
        task = self._tasks.get(sid)
        if task is None or task.done():
            return
        if sid not in self.comp.committing:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def _fire(self, rt: SessionRuntime, delay: float) -> None:
        from auoreo.runtime.compaction import CompactionError

        await asyncio.sleep(delay)
        if rt.running or rt.inbox.next_turn or self.comp.tripped(rt.session_id):
            return
        model = self.comp._model(rt)
        if self.comp.tokens(model, rt.history) < self.s.settings.compaction.idle_min_tokens:
            return
        with contextlib.suppress(CompactionError):  # failures are emitted + counted by the service
            await self.comp.compact(rt.session_id, trigger="idle", rt=rt, model=model)

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


__all__ = ["IdleCompactor"]
