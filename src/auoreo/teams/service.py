"""TeamsService (spec §16.1–16.4): spawn child sessions, teams, liveness, wait/interrupt.

A delegated task is a child session (`sessions.parent_id`) in the parent's team. Its
prompt is submitted non-interactively-as-the-parent through the coordinator; its result
is the child's final text plus usage summed from the child's `usage` rows. Children are
capped per parent (`teams_max_children`) and per process (`FLEET_CAP`, `teams.fleet`);
extra spawns queue. Roles are depth-derived (`teams.ceiling.role_for`): a leaf's engine
denies `LEAF_DENIED`. Child spend counts toward the root's `max_cost_usd`. A running turn
is watched by `teams.monitor.ChildMonitor` (live log, heartbeat, staleness, budget);
control verbs (wait/stop/steer/resume/pause) live in `teams.control`.
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
from auoreo.teams import spawn_i3
from auoreo.teams.board import Board
from auoreo.teams.ceiling import CeilingEngine, clamp, leaf_permissions, role_for
from auoreo.teams.child_run import run_child
from auoreo.teams.control import WAIT_DEFAULT_MS, WAIT_MAX_MS, WAIT_MIN_MS, Control
from auoreo.teams.fleet import FLEET_CAP, Fleet
from auoreo.teams.fork import ExactSpec, FrozenTurn, fork_seed
from auoreo.teams.format import HANDOFF_SCHEMA, summarize, task_notification
from auoreo.teams.kanban import KanbanStore
from auoreo.teams.mailbox import LEAD, Mailbox, TeamsError
from auoreo.teams.monitor import Budget
from auoreo.teams.scope import workflow_team
from auoreo.teams.supervisor import Supervisor

if TYPE_CHECKING:
    from auoreo.core.services import Services

__all__ = [
    "FLEET_CAP",
    "WAIT_DEFAULT_MS",
    "WAIT_MAX_MS",
    "WAIT_MIN_MS",
    "Member",
    "TeamsError",
    "TeamsService",
    "start",
]

TASK_NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")
MODES = ("spawn", "fork", "exact")


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
    role: str = "leaf"
    model: str | None = None
    schema: dict[str, Any] | None = None
    interactive: bool = False
    budget: Budget = field(default_factory=Budget)
    subagent_id: str = ""  # --- I3: sa-{n}-{hex8}
    runs: int = 0  # turns run so far (live log `task-<n>.log`)
    allowed_tools: list[str] | None = None
    extra_deny: list[str] = field(default_factory=list)
    exact: ExactSpec | None = None
    handoff: bool = False
    handoff_data: dict[str, Any] | None = None

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
        self.fleet = Fleet(self)
        self.control = Control(self)
        self.roles: dict[str, str] = {}  # child session id → orchestrator | leaf
        self.last_activity: dict[str, float] = {}  # parent session id → last child heartbeat
        self.synthetic: dict[str, Any] = {}  # --- I3: session id → recorded synthetic_output
        self.forks: set[str] = set()  # exact/summary fork children (recursion guard)
        self.sa_counters: dict[str, int] = {}  # parent session id → spawns so far

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
        mode: str = "spawn",
        budget: dict[str, Any] | None = None,
        allowed_tools: list[str] | None = None,
        inherit_mcp_toolsets: bool | list[str] = False,
        handoff: bool = False,
        frozen: FrozenTurn | None = None,
    ) -> Member:
        parent = await self.s.get_session(parent_session_id)
        if parent is None:
            raise TeamsError(f"unknown parent session '{parent_session_id}'")
        await self.check_can_spawn(parent_session_id)
        self.supervisor.check_spawn(parent_session_id, parent.agent, role, output_schema)
        if not TASK_NAME_RE.fullmatch(name):
            raise TeamsError(f"invalid task name '{name}': use [a-z0-9_]+ (≤64 chars)")
        if mode not in MODES:
            raise TeamsError(f"unknown mode '{mode}' (known: {', '.join(MODES)})")
        if mode != "spawn" and parent_session_id in self.forks:  # boilerplate recursion guard
            raise TeamsError("a forked subagent cannot fork again; use mode=spawn or do the work")
        tools = self.s.harness.tools
        allowed = spawn_i3.check_allowed(tools, allowed_tools)  # unknown names fail loud
        extra_deny = spawn_i3.mcp_deny(tools, inherit_mcp_toolsets)
        if handoff and output_schema is None:
            output_schema = HANDOFF_SCHEMA
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
        member = Member(team.id, name, agent, child.id, parent_session_id, depth, background, model=model)
        member.subagent_id = spawn_i3.next_subagent_id(self.sa_counters, parent_session_id)
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
                        **spawn_i3.member_extras(self, member, child_manifest),
                    )
                )
                await s.commit()
        except IntegrityError as exc:
            raise TeamsError(f"name '{name}' is already taken in team {team.name}") from exc

        # Permission ceiling: the child can never run with more power than its parent;
        # a leaf additionally loses the LEAF_DENIED tools (executor floor, spec §16.3).
        child_role = role_for(self.s.settings.teams_orchestrator_enabled, depth)
        denied = leaf_permissions(self.s.harness.tools) if child_role == "leaf" else frozenset()
        parent_mode = self._mode_of(parent_session_id, parent.agent)
        child_mode = child_manifest.permission_mode or self.s.settings.permission_mode
        crt = await self.s.coordinator.runtime(child.id)
        crt.engine = CeilingEngine(
            parent_mode,
            [],
            [],
            clamp(child_mode, parent_mode),
            str(self.s.cwd / ".auoreo" / "plans"),
            denied=denied | spawn_i3.denied_permissions(tools, allowed, extra_deny),
        )
        prt = await self.s.coordinator.runtime(parent_session_id)
        member.allowed_tools, member.extra_deny, member.handoff = allowed, extra_deny, handoff
        if mode == "exact" and (seeded := await spawn_i3.seed_exact(self, member, frozen, prompt)):
            prompt = seeded
        elif mode != "spawn":  # `fork`, or an `exact` cache miss
            self.forks.add(child.id)
            prompt = fork_seed(prt.history, self.s.settings.teams2.delegation.fork_seed_max_chars) + prompt
        plan = Budget.of(budget)
        member.role, member.schema = child_role, output_schema
        member.interactive, member.budget = prt.interactive, plan
        spawn_i3.register_usage(member)
        self.members_live[child.id] = member
        self.roles[child.id] = child_role
        await self.supervisor.on_spawn(member, parent.agent, role, prompt)
        ms = plan.timeout_ms
        timeout = ms / 1000 if ms else float(timeout_s or self.s.settings.teams_child_timeout_s)
        return await self.launch(member, prompt, timeout)

    async def launch(self, m: Member, prompt: str, timeout: float) -> Member:
        """Run one turn of `m` as a task; foreground awaits it, background returns at once."""
        task = asyncio.create_task(self._run(m, prompt, timeout), name=f"subagent:{m.session_id}")
        self._tasks[m.session_id] = task
        if m.background:
            return m
        return await task

    async def check_can_spawn(self, parent_session_id: str) -> None:
        """Refusals that apply to every new child: pause switch, leaf parent, spent budget."""
        if self.control.paused:
            raise TeamsError("delegation is paused (delegation.pause); new spawns are refused")
        if self.roles.get(parent_session_id) == "leaf":
            raise TeamsError("delegate_task is not available to leaf subagents; do the task yourself")
        cap = self.s.settings.max_cost_usd
        if await self.s.coordinator.runner.usage.over_budget(parent_session_id, cap):
            raise TeamsError(f"BUDGET: the session tree reached max_cost_usd={cap}; no new children")

    def member_from_row(self, row: TeamMemberRow, parent: str) -> Member:
        """A `Member` for a child known only from the database (e.g. after a restart)."""
        m = Member(row.team_id, row.name, row.agent, row.session_id, parent, row.depth, model=row.model)
        m.subagent_id = row.subagent_id or ""
        spawn_i3.register_usage(m)
        m.role = self.roles.get(row.session_id) or role_for(
            self.s.settings.teams_orchestrator_enabled, row.depth
        )
        m.status = row.status
        self.members_live[row.session_id] = m
        return m

    def _mode_of(self, session_id: str, agent: str) -> str:
        engine = self.s.coordinator.engine_for(session_id)
        if engine is not None:
            return str(engine.mode)
        manifest = self.s.harness.agent(agent).manifest
        return manifest.permission_mode or self.s.settings.permission_mode

    async def _run(self, m: Member, prompt: str, timeout: float) -> Member:
        return await run_child(self, m, prompt, timeout)  # teams.child_run

    async def inject(self, session_id: str, text: str) -> None:
        """Deliver `text` as a user message at the session's next step, without waking it."""
        rt = await self.s.coordinator.runtime(session_id)
        await self.s.coordinator.submit(session_id, text, "inject", interactive=rt.interactive)

    # ------------------------------------------------------------ control (teams.control)
    async def wait(self, team_id: str, name: str, timeout_ms: int | None = None) -> dict[str, Any]:
        return await self.control.wait(team_id, name, timeout_ms)

    async def interrupt(self, team_id: str, name: str) -> bool:
        return await self.control.interrupt(team_id, name)


async def start(services: Services) -> TeamsService:
    svc = TeamsService(services)
    svc.start()
    return svc
