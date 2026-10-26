"""Controlling delegated children (spec §16.1, §16.4, §16.5): wait, interrupt, list, steer, stop,
resume, and the `delegation.pause` switch.

`list|steer|stop` are synchronous and bypass the pause switch and the depth limit. A child is
addressed by its task id (= child session id) or by its name in the caller's team.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from auoreo.core.db import SessionRow, TeamMemberRow
from auoreo.teams.mailbox import TeamsError

if TYPE_CHECKING:
    from auoreo.teams.service import Member, TeamsService

WAIT_MIN_MS, WAIT_MAX_MS, WAIT_DEFAULT_MS = 1_000, 300_000, 30_000


class Control:
    def __init__(self, teams: TeamsService) -> None:
        self.t = teams
        self.pause_override: bool | None = None

    # ------------------------------------------------------------ pause
    @property
    def paused(self) -> bool:
        if self.pause_override is not None:
            return self.pause_override
        return bool(self.t.s.settings.teams2.delegation.pause)

    async def set_paused(self, paused: bool, actor: str = "api") -> bool:
        self.pause_override = paused
        await self.t.s.emit("server", "delegation.paused", {"paused": paused, "actor": actor})
        await self.t.s.audit.record(
            actor=actor, action="delegation.pause", decision="allow", detail={"paused": paused}
        )
        return paused

    # ------------------------------------------------------------ addressing
    async def child(self, parent: str, ref: str) -> TeamMemberRow:
        """The caller's own child by task id (session id) or member name."""
        async with self.t.s.db.session() as s:
            q = (
                select(TeamMemberRow)
                .join(SessionRow, SessionRow.id == TeamMemberRow.session_id)
                .where(SessionRow.parent_id == parent)
            )
            rows = list((await s.execute(q)).scalars())
        for row in rows:
            if ref in (row.session_id, row.name):
                return row
        known = ", ".join(f"{r.name} ({r.session_id})" for r in rows) or "none"
        raise TeamsError(f"no subagent '{ref}' among this session's children (known: {known})")

    async def children(self, parent: str) -> list[dict[str, Any]]:
        async with self.t.s.db.session() as s:
            q = (
                select(TeamMemberRow)
                .join(SessionRow, SessionRow.id == TeamMemberRow.session_id)
                .where(SessionRow.parent_id == parent)
                .order_by(TeamMemberRow.id)
            )
            rows = list((await s.execute(q)).scalars())
        out = []
        for row in rows:
            live = self.t.members_live.get(row.session_id)
            out.append(
                {
                    "subagent_id": row.session_id,
                    "name": row.name,
                    "agent": row.agent,
                    "depth": row.depth,
                    "role": self.t.roles.get(row.session_id),
                    "status": live.status if live else row.status,
                    "running": self.t.is_running(row.session_id),
                    "usage": live.usage if live else {},
                }
            )
        return out

    # ------------------------------------------------------------ steer / stop / resume
    async def steer(self, parent: str, ref: str, message: str) -> dict[str, Any]:
        row = await self.child(parent, ref)
        if not self.t.is_running(row.session_id):
            raise TeamsError(f"subagent '{row.name}' is not running; use resume_agent to give it new work")
        await self.t.inject(row.session_id, f"[steer from parent] {message}")
        await self.t.s.emit(
            parent, "subagent.steered", {"parent": parent, "child": row.session_id, "name": row.name}
        )
        return {"subagent_id": row.session_id, "name": row.name, "steered": True}

    async def stop(self, parent: str, ref: str) -> dict[str, Any]:
        row = await self.child(parent, ref)
        stopped = await self.stop_session(row.session_id)
        return {"subagent_id": row.session_id, "name": row.name, "stopped": stopped}

    async def stop_session(self, sid: str) -> bool:
        cancelled = await self.t.s.coordinator.cancel(sid)
        task = self.t._tasks.get(sid)
        if task is not None and not task.done():
            if not cancelled:  # still queued: stop it before it starts
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(task), 5)
            cancelled = True
        return cancelled

    async def resume(self, parent: str, ref: str, prompt: str, background: bool) -> Member:
        """Give a finished child a new prompt: its session wakes and runs one more turn with
        the same role, ceiling and budget rules; the result comes back like a spawn's."""
        row = await self.child(parent, ref)
        if self.t.is_running(row.session_id):
            raise TeamsError(f"subagent '{row.name}' is still running; steer it or wait for it")
        if self.paused:
            raise TeamsError("delegation is paused (delegation.pause); resume is refused")
        m = self.t.members_live.get(row.session_id) or self.t.member_from_row(row, parent)
        m.background = background
        m.text, m.error = "", None
        await self.t.s.emit(
            parent, "subagent.resumed", {"parent": parent, "child": m.session_id, "name": m.name}
        )
        return await self.t.launch(m, prompt, timeout=float(self.t.s.settings.teams_child_timeout_s))

    # ------------------------------------------------------------ wait / interrupt
    async def wait(self, team_id: str, name: str, timeout_ms: int | None = None) -> dict[str, Any]:
        """Long-poll until `name`'s current run finishes (clamped 1 s–300 s, default 30 s)."""
        team = await self.t.get_team(team_id)
        if team is None:
            raise TeamsError(f"unknown team '{team_id}'")
        sid = await self.t.session_for(team, name)
        ms = max(WAIT_MIN_MS, min(WAIT_MAX_MS, timeout_ms or WAIT_DEFAULT_MS))
        task = self.t._tasks.get(sid)
        timed_out = False
        if task is not None and not task.done():  # spawned run (also covers queued)
            await asyncio.wait({task}, timeout=ms / 1000)
            timed_out = not task.done()
        else:  # a later run, e.g. woken by mail
            try:
                await self.t.s.coordinator.wait(sid, ms / 1000)
            except TimeoutError:
                timed_out = True
        m = self.t.members_live.get(sid)
        return {
            "name": name,
            "session_id": sid,
            "timed_out": timed_out,
            "running": self.t.is_running(sid),
            "status": m.status if m else ("running" if self.t.is_running(sid) else "idle"),
            "result": m.text if m else "",
        }

    async def interrupt(self, team_id: str, name: str) -> bool:
        team = await self.t.get_team(team_id)
        if team is None:
            raise TeamsError(f"unknown team '{team_id}'")
        return await self.stop_session(await self.t.session_for(team, name))
