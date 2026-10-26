"""Fan-out limits (spec §25.11): at most `teams_max_children` running children per parent and
`FLEET_CAP` per process; extra spawns wait in FIFO order (`subagent.queued` with position)."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from auoreo.teams.service import Member, TeamsService

FLEET_CAP = 9


class Fleet:
    def __init__(self, teams: TeamsService) -> None:
        self.t = teams
        self.running: dict[str, int] = {}
        self.total = 0
        self.waiters: list[tuple[str, asyncio.Future[None]]] = []

    def can_start(self, parent: str) -> bool:
        return self.running.get(parent, 0) < self.t.s.settings.teams_max_children and self.total < FLEET_CAP

    def take(self, parent: str) -> None:
        self.running[parent] = self.running.get(parent, 0) + 1
        self.total += 1

    async def acquire(self, m: Member) -> None:
        parent = m.parent_session_id
        if not self.waiters and self.can_start(parent):
            self.take(parent)
            return
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.waiters.append((parent, fut))
        position = sum(1 for p, _ in self.waiters if p == parent)
        await self.t.s.emit(
            parent,
            "subagent.queued",
            {"parent": parent, "child": m.session_id, "name": m.name, "agent": m.agent, "position": position},
        )
        try:
            await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():  # slot was handed over: give it back
                self.release(parent)
            with contextlib.suppress(ValueError):
                self.waiters.remove((parent, fut))
            raise

    def release(self, parent: str) -> None:
        self.running[parent] = max(0, self.running.get(parent, 0) - 1)
        self.total = max(0, self.total - 1)
        for entry in list(self.waiters):
            p, fut = entry
            if fut.done():
                self.waiters.remove(entry)
                continue
            if self.can_start(p):
                self.waiters.remove(entry)
                self.take(p)
                fut.set_result(None)
