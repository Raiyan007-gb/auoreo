"""TeamsService (spec §16.1–16.4): spawn child sessions, teams, liveness, wait/interrupt.

A delegated task is a child session (`sessions.parent_id`) in the parent's team. Its
prompt is submitted non-interactively-as-the-parent through the coordinator; its result
is the child's final text plus usage summed from the child's `usage` rows. Children are
capped per parent (`teams_max_children`) and per process (`FLEET_CAP`); extra spawns queue.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from auoreo.core.db import TeamMemberRow, TeamRow
from auoreo.runtime.hooks import subagent_stop
from auoreo.runtime.inbox import RunOptions
from auoreo.teams.board import Board
from auoreo.teams.ceiling import CeilingEngine, clamp
from auoreo.teams.format import (
    SCHEMA_MISMATCH,
    check_output,
    retry_instruction,
    schema_instruction,
    summarize,
    task_notification,
)
from auoreo.teams.kanban import KanbanStore
from auoreo.teams.mailbox import LEAD, Mailbox, TeamsError
from auoreo.teams.scope import workflow_team
from auoreo.teams.supervisor import Supervisor

if TYPE_CHECKING:
    from auoreo.core.services import Services

__all__ = ["FLEET_CAP", "Member", "TeamsError", "TeamsService", "start"]

FLEET_CAP = 9
TASK_NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")
WAIT_MIN_MS, WAIT_MAX_MS, WAIT_DEFAULT_MS = 1_000, 300_000, 30_000
_FINAL = {"completed": "done", "max_steps": "done", "stopped": "stopped"}


@dataclass
class Member:
    """One delegated task: a child session and, once finished, its result."""

    team_id: str
    name: str
    agent: str
    session_id: str
    parent_session_id: str
    depth: int
    background: bool = False
    status: str = "queued"  # queued|running|completed|failed|stopped
    text: str = ""
    error: dict[str, Any] | None = None
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def task_id(self) -> str:
        return self.session_id

    def notification(self) -> str:
        detail = self.text or (self.error or {}).get("message", "") or self.status
        code = f" [{self.error['code']}]" if self.error and self.error.get("code") else ""
        summary = f"{self.name} ({self.agent}) {self.status}{code}: {summarize(detail)}"
        return task_notification(self.task_id, self.status, summary, self.text or detail, self.usage)


class TeamsService:
    def __init__(self, services: Services) -> None:
        self.s = services
        self.kanban = KanbanStore(services)
        self.mail = Mailbox(self)
        self.board = Board(services)  # --- W1-B
        self.supervisor = Supervisor(self)
        self.join_lock = asyncio.Lock()  # workflow steps joining a team (teams.scope)
        self.members_live: dict[str, Member] = {}  # by child session id
        self._tasks: dict[str, asyncio.Task[Member]] = {}
        self._running: dict[str, int] = {}
        self._fleet = 0
        self._waiters: list[tuple[str, asyncio.Future[None]]] = []

    @property
    def max_depth(self) -> int:
        return self.s.settings.teams_max_depth

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.kanban.start()

    async def close(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        for task in list(self._tasks.values()):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self.kanban.close()

    # ------------------------------------------------------------ lookups
    async def depth(self, session_id: str) -> int:
        """Number of ancestors via `sessions.parent_id` (a top-level session is 0)."""
        depth, current = 0, await self.s.get_session(session_id)
        while current is not None and current.parent_id and depth < 64:
            depth += 1
            current = await self.s.get_session(current.parent_id)
        return depth

    async def get_team(self, team_id: str) -> TeamRow | None:
        async with self.s.db.session() as s:
            return await s.get(TeamRow, team_id)

    async def list_teams(self) -> list[TeamRow]:
        async with self.s.db.session() as s:
            return list((await s.execute(select(TeamRow).order_by(TeamRow.created_at))).scalars())

    async def team_of(self, session_id: str) -> TeamRow | None:
        """The team `session_id` leads, else the team it is a member of."""
        async with self.s.db.session() as s:
            led = (await s.execute(select(TeamRow).where(TeamRow.lead_session_id == session_id))).scalar()
            if led is not None:
                return led
            mem = (
                await s.execute(select(TeamMemberRow).where(TeamMemberRow.session_id == session_id))
            ).scalar()
            return await s.get(TeamRow, mem.team_id) if mem is not None else None

    async def members(self, team_id: str) -> list[TeamMemberRow]:
        async with self.s.db.session() as s:
            q = select(TeamMemberRow).where(TeamMemberRow.team_id == team_id).order_by(TeamMemberRow.id)
            return list((await s.execute(q)).scalars())

    async def member(self, team_id: str, name: str) -> TeamMemberRow | None:
        async with self.s.db.session() as s:
            q = select(TeamMemberRow).where(TeamMemberRow.team_id == team_id, TeamMemberRow.name == name)
            return (await s.execute(q)).scalar()

    async def name_in_team(self, team: TeamRow, session_id: str) -> str:
        if session_id == team.lead_session_id:
            return LEAD
        for m in await self.members(team.id):
            if m.session_id == session_id:
                return m.name
        raise TeamsError(f"session {session_id} is not in team {team.name}")

    async def session_for(self, team: TeamRow, name: str) -> str:
        if name == LEAD:
            return team.lead_session_id
        row = await self.member(team.id, name)
        if row is None:
            known = [LEAD, *(m.name for m in await self.members(team.id))]
            raise TeamsError(f"no peer named '{name}' in team {team.name} (peers: {', '.join(known)})")
        return row.session_id

    def is_running(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        return bool(task and not task.done()) or self.s.coordinator.is_running(session_id)

    async def peers(self, team_id: str) -> list[dict[str, Any]]:
        team = await self.get_team(team_id)
        if team is None:
            raise TeamsError(f"unknown team '{team_id}'")
        out = [
            {
                "name": LEAD,
                "agent": (row.agent if (row := await self.s.get_session(team.lead_session_id)) else ""),
                "session_id": team.lead_session_id,
                "depth": await self.depth(team.lead_session_id),
                "status": "lead",
                "running": self.s.coordinator.is_running(team.lead_session_id),
            }
        ]
        for m in await self.members(team_id):
            out.append(
                {
                    "name": m.name,
                    "agent": m.agent,
                    "session_id": m.session_id,
                    "depth": m.depth,
                    "status": m.status,
                    "running": self.is_running(m.session_id),
                }
            )
        return out

    # ------------------------------------------------------------ teams
    async def create_team(
        self, lead_session_id: str, name: str, description: str = "", *, team_id: str | None = None
    ) -> TeamRow:
        if await self.team_of(lead_session_id) is not None:
            raise TeamsError("this session already belongs to a team (one team per lead session)")
        row = TeamRow(
            id=team_id or f"t_{uuid.uuid4().hex[:12]}",
            name=name,
            description=description,
            lead_session_id=lead_session_id,
        )
        try:
            async with self.s.db.session() as s:
                s.add(row)
                await s.commit()
        except IntegrityError as exc:
            raise TeamsError(f"a team named '{name}' already exists") from exc
        await self.s.emit(lead_session_id, "team.created", {"team": row.id, "name": name})
        return row

    async def team_for(self, session_id: str) -> TeamRow | None:
        """`team_of`, else the team a workflow step joins (see `teams.scope`)."""
        return await self.team_of(session_id) or await workflow_team(self, session_id)

    async def ensure_team(self, session_id: str) -> TeamRow:
        team = await self.team_for(session_id)
        if team is not None:
            return team
        auto = f"t_{uuid.uuid4().hex[:12]}"
        return await self.create_team(session_id, auto, team_id=auto)

    # ------------------------------------------------------------ spawn
    async def spawn(
        self,
        parent_session_id: str,
        *,
        agent: str,
        prompt: str,
        name: str,
        background: bool,
        model: str | None = None,
        timeout_s: float | None = None,
        output_schema: dict[str, Any] | None = None,
        role: str | None = None,
    ) -> Member:
        parent = await self.s.get_session(parent_session_id)
        if parent is None:
            raise TeamsError(f"unknown parent session '{parent_session_id}'")
        self.supervisor.check_spawn(parent_session_id, parent.agent, role, output_schema)
        if not TASK_NAME_RE.fullmatch(name):
            raise TeamsError(f"invalid task name '{name}': use [a-z0-9_]+ (≤64 chars)")
        depth = await self.depth(parent_session_id) + 1
        if depth > self.max_depth:
            raise TeamsError(
                f"delegation depth limit reached: a child here would be at depth {depth} "
                f"(teams_max_depth={self.max_depth}); do the work yourself"
            )
        try:
            child_manifest = self.s.harness.agent(agent).manifest
        except KeyError as exc:
            raise TeamsError(f"unknown agent '{agent}' (known: {sorted(self.s.harness.agents)})") from exc
        team = await self.ensure_team(parent_session_id)
        if await self.member(team.id, name) is not None or name == LEAD:
            raise TeamsError(f"name '{name}' is already taken in team {team.name}")
        base = {"parent": parent_session_id, "name": name, "agent": agent}
        await self.s.emit(parent_session_id, "subagent.spawn_requested", {**base, "background": background})

        child = await self.s.create_session(title=name, agent=agent, model=model, parent_id=parent_session_id)
        try:
            async with self.s.db.session() as s:
                s.add(
                    TeamMemberRow(
                        team_id=team.id,
                        name=name,
                        agent=agent,
                        session_id=child.id,
                        depth=depth,
                        status="queued",
                    )
                )
                await s.commit()
        except IntegrityError as exc:
            raise TeamsError(f"name '{name}' is already taken in team {team.name}") from exc

        # Permission ceiling: the child can never run with more power than its parent.
        parent_mode = self._mode_of(parent_session_id, parent.agent)
        child_mode = child_manifest.permission_mode or self.s.settings.permission_mode
        crt = await self.s.coordinator.runtime(child.id)
        crt.engine = CeilingEngine(
            parent_mode, [], [], clamp(child_mode, parent_mode), str(self.s.cwd / ".auoreo" / "plans")
        )
        prt = await self.s.coordinator.runtime(parent_session_id)
        member = Member(team.id, name, agent, child.id, parent_session_id, depth, background)
        self.members_live[child.id] = member
        await self.supervisor.on_spawn(member, parent.agent, role, prompt)
        timeout = float(timeout_s or self.s.settings.teams_child_timeout_s)
        task = asyncio.create_task(
            self._run(member, prompt, timeout, prt.interactive, model, output_schema),
            name=f"subagent:{child.id}",
        )
        self._tasks[child.id] = task
        if background:
            return member
        return await task

    def _mode_of(self, session_id: str, agent: str) -> str:
        engine = self.s.coordinator.engine_for(session_id)
        if engine is not None:
            return str(engine.mode)
        manifest = self.s.harness.agent(agent).manifest
        return manifest.permission_mode or self.s.settings.permission_mode

    async def _run(
        self,
        m: Member,
        prompt: str,
        timeout: float,
        interactive: bool,
        model: str | None,
        schema: dict[str, Any] | None,
    ) -> Member:
        base = {"parent": m.parent_session_id, "child": m.session_id, "name": m.name, "agent": m.agent}
        try:
            await self._acquire(m)
        except asyncio.CancelledError:
            await self._finish(m, "stopped", "", {"code": "CANCELLED", "message": "interrupted while queued"})
            raise
        try:
            m.status = "running"
            await self._set_status(m, "running")
            await self.s.emit(m.parent_session_id, "subagent.started", base)
            text = prompt + ("\n\n" + schema_instruction(schema) if schema else "")
            status, out, error = await self._turn(m, text, timeout, interactive, model)
            if schema and status == "completed" and (problem := check_output(out, schema)) is not None:
                await self.s.emit(m.parent_session_id, "subagent.retry", {**base, "error": problem})
                status, out, error = await self._turn(
                    m, retry_instruction(problem, schema), timeout, interactive, model
                )
                if status == "completed" and (problem := check_output(out, schema)) is not None:
                    status, error = "failed", {"code": SCHEMA_MISMATCH, "message": problem}
        except asyncio.CancelledError:
            await self.s.coordinator.cancel(m.session_id)  # the parent went away: stop the child
            await self._finish(m, "stopped", "", {"code": "CANCELLED", "message": "interrupted"})
            raise
        except Exception as exc:  # a teams bug must not leave the member "running"
            status, out, error = "failed", "", {"code": "INTERNAL", "message": f"{type(exc).__name__}: {exc}"}
        finally:
            self._release(m.parent_session_id)
        await self._finish(m, status, out, error)
        return m

    async def _turn(
        self, m: Member, text: str, timeout: float, interactive: bool, model: str | None
    ) -> tuple[str, str, dict[str, Any] | None]:
        coord = self.s.coordinator
        run_id = await coord.submit(
            m.session_id,
            text,
            "followup",
            agent=m.agent,
            model=model,
            interactive=interactive,
            options=RunOptions(skip_memory=True),  # subagents run without memory (spec §16.1)
        )
        if run_id is None:  # handled by a slash command (e.g. `/model`)
            return "completed", "", None
        try:
            await coord.wait(m.session_id, timeout)
        except TimeoutError:
            await coord.cancel(m.session_id)
            return "failed", "", {"code": "TIMEOUT", "message": f"child exceeded {timeout:.0f}s"}
        result = coord.result(run_id)
        if result is None:
            return "stopped", "", {"code": "CANCELLED", "message": "interrupted"}
        status = "completed" if result.status in {"completed", "max_steps"} else "failed"
        return status, result.text, result.error

    async def _finish(self, m: Member, status: str, text: str, error: dict[str, Any] | None) -> None:
        m.status, m.text, m.error = status, text, error
        m.usage = await self.s.coordinator.runner.usage.totals(
            await self.s.coordinator.runner.usage.session_tree(m.session_id)
        )
        await self._set_status(m, _FINAL.get(status, "failed"))
        data = {
            "parent": m.parent_session_id,
            "child": m.session_id,
            "name": m.name,
            "agent": m.agent,
            "status": status,
            "usage": m.usage,
            **({"error": error} if error else {}),
        }
        kind = "completed" if status == "completed" else "failed"
        await self.s.emit(m.parent_session_id, f"subagent.{kind}", data)
        await subagent_stop(self.s, data)
        await self.supervisor.on_finish(m)
        if m.background:
            with contextlib.suppress(Exception):  # the parent may have been deleted
                await self.inject(m.parent_session_id, m.notification())

    async def _set_status(self, m: Member, status: str) -> None:
        async with self.s.db.session() as s:
            q = select(TeamMemberRow).where(TeamMemberRow.session_id == m.session_id)
            row = (await s.execute(q)).scalar()
            if row is not None:
                row.status = status
                await s.commit()

    async def inject(self, session_id: str, text: str) -> None:
        """Deliver `text` as a user message at the session's next step, without waking it."""
        rt = await self.s.coordinator.runtime(session_id)
        await self.s.coordinator.submit(session_id, text, "inject", interactive=rt.interactive)

    # ------------------------------------------------------------ concurrency
    def _can_start(self, parent: str) -> bool:
        return self._running.get(parent, 0) < self.s.settings.teams_max_children and self._fleet < FLEET_CAP

    def _take(self, parent: str) -> None:
        self._running[parent] = self._running.get(parent, 0) + 1
        self._fleet += 1

    async def _acquire(self, m: Member) -> None:
        parent = m.parent_session_id
        if not self._waiters and self._can_start(parent):
            self._take(parent)
            return
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append((parent, fut))
        position = sum(1 for p, _ in self._waiters if p == parent)
        await self.s.emit(
            parent,
            "subagent.queued",
            {"parent": parent, "child": m.session_id, "name": m.name, "agent": m.agent, "position": position},
        )
        try:
            await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():  # slot was handed over: give it back
                self._release(parent)
            with contextlib.suppress(ValueError):
                self._waiters.remove((parent, fut))
            raise

    def _release(self, parent: str) -> None:
        self._running[parent] = max(0, self._running.get(parent, 0) - 1)
        self._fleet = max(0, self._fleet - 1)
        for entry in list(self._waiters):
            p, fut = entry
            if fut.done():
                self._waiters.remove(entry)
                continue
            if self._can_start(p):
                self._waiters.remove(entry)
                self._take(p)
                fut.set_result(None)

    # ------------------------------------------------------------ control
    async def wait(self, team_id: str, name: str, timeout_ms: int | None = None) -> dict[str, Any]:
        """Long-poll until `name`'s current run finishes (clamped 1 s–300 s, default 30 s)."""
        team = await self.get_team(team_id)
        if team is None:
            raise TeamsError(f"unknown team '{team_id}'")
        sid = await self.session_for(team, name)
        ms = max(WAIT_MIN_MS, min(WAIT_MAX_MS, timeout_ms or WAIT_DEFAULT_MS))
        task = self._tasks.get(sid)
        timed_out = False
        if task is not None and not task.done():  # spawned run (also covers queued)
            await asyncio.wait({task}, timeout=ms / 1000)
            timed_out = not task.done()
        else:  # a later run, e.g. woken by mail
            try:
                await self.s.coordinator.wait(sid, ms / 1000)
            except TimeoutError:
                timed_out = True
        m = self.members_live.get(sid)
        return {
            "name": name,
            "session_id": sid,
            "timed_out": timed_out,
            "running": self.is_running(sid),
            "status": m.status if m else ("running" if self.is_running(sid) else "idle"),
            "result": m.text if m else "",
        }

    async def interrupt(self, team_id: str, name: str) -> bool:
        team = await self.get_team(team_id)
        if team is None:
            raise TeamsError(f"unknown team '{team_id}'")
        sid = await self.session_for(team, name)
        cancelled = await self.s.coordinator.cancel(sid)
        task = self._tasks.get(sid)
        if task is not None and not task.done():
            if not cancelled:  # still queued: stop it before it starts
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(task), 5)
            cancelled = True
        return cancelled


async def start(services: Services) -> TeamsService:
    svc = TeamsService(services)
    svc.start()
    return svc
