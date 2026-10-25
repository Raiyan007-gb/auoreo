"""Supervisor opt-in (spec §16.3, L1024): `teams_orchestrator_enabled=true` lets a coordinator
(or a child spawned with `role="supervisor"`) run a supervisor loop.

Rules while enabled:
- every spawn made by a supervisor must pass `output_schema` (results are machine-checked);
- each continue/spawn/finish decision is logged as a `team.decision` event on the `server`
  aggregate: `{team_id, child, outcome, decision, reason}`. Spawns log `spawn`; a finished
  child logs the decision the continue-vs-spawn matrix below recommends for its outcome;
  mail from the supervisor to an idle child logs `continue`.

While disabled, spawning with `role="supervisor"` is refused and nothing is logged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from auoreo.core.db import EventRow
from auoreo.teams.format import SCHEMA_MISMATCH
from auoreo.teams.mailbox import TeamsError

if TYPE_CHECKING:
    from auoreo.teams.service import Member, TeamsService

ROLES = ("worker", "supervisor")
SUPERVISOR_AGENTS = frozenset({"coordinator"})
DECISIONS = ("continue", "spawn", "finish")
# outcome (child status or error code) → (decision, reason): the logged continue-vs-spawn matrix.
MATRIX: dict[str, tuple[str, str]] = {
    "completed": ("finish", "output is valid for its output_schema; integrate it"),
    SCHEMA_MISMATCH: (
        "spawn",
        "output failed the schema after one retry; spawn a fresh child with a narrower goal",
    ),
    "TIMEOUT": ("continue", "child ran out of time; continue it with a narrower scope"),
    "CANCELLED": ("finish", "child was interrupted; nothing left to run"),
    "stopped": ("finish", "child was stopped; nothing left to run"),
    "failed": ("spawn", "child failed; spawn a fresh child with the error as context"),
}


def outcome_of(m: Member) -> str:
    code = (m.error or {}).get("code")
    return str(code) if code in MATRIX else m.status


class Supervisor:
    def __init__(self, teams: TeamsService) -> None:
        self.t = teams
        self.roles: dict[str, str] = {}  # child session id → role it was spawned with

    @property
    def enabled(self) -> bool:
        return bool(self.t.s.settings.teams_orchestrator_enabled)

    def is_supervisor(self, session_id: str, agent: str) -> bool:
        if not self.enabled:
            return False
        return self.roles.get(session_id) == "supervisor" or agent in SUPERVISOR_AGENTS

    def check_spawn(
        self,
        parent_session_id: str,
        parent_agent: str,
        role: str | None,
        output_schema: dict[str, Any] | None,
    ) -> None:
        if role is not None and role not in ROLES:
            raise TeamsError(f"unknown role '{role}' (known: {', '.join(ROLES)})")
        if role == "supervisor" and not self.enabled:
            raise TeamsError("role=supervisor needs teams_orchestrator_enabled=true (supervisors are opt-in)")
        if self.is_supervisor(parent_session_id, parent_agent) and not output_schema:
            raise TeamsError(
                "a supervisor must pass output_schema on every spawn (teams_orchestrator_enabled=true)"
            )

    async def decide(
        self, team_id: str, child: str, outcome: str, decision: str, reason: str
    ) -> dict[str, Any]:
        if decision not in DECISIONS:
            raise TeamsError(f"unknown decision '{decision}' (known: {', '.join(DECISIONS)})")
        data = {
            "team_id": team_id,
            "child": child,
            "outcome": outcome,
            "decision": decision,
            "reason": reason[:500],
        }
        await self.t.s.emit("server", "team.decision", data)
        return data

    async def on_spawn(self, m: Member, parent_agent: str, role: str | None, goal: str) -> None:
        if role:
            self.roles[m.session_id] = role
        if self.is_supervisor(m.parent_session_id, parent_agent):
            await self.decide(m.team_id, m.name, "new", "spawn", f"delegated to {m.agent}: {goal[:120]}")

    async def on_finish(self, m: Member) -> None:
        parent = await self.t.s.get_session(m.parent_session_id)
        if parent is None or not self.is_supervisor(parent.id, parent.agent):
            return
        outcome = outcome_of(m)
        decision, reason = MATRIX.get(outcome, MATRIX["failed"])
        await self.decide(m.team_id, m.name, outcome, decision, reason)

    async def on_mail(self, team_id: str, sender_session: str, to: str) -> None:
        """Mail from a supervisor to a finished child is a `continue` decision."""
        sender = await self.t.s.get_session(sender_session)
        if sender is None or not self.is_supervisor(sender.id, sender.agent):
            return
        row = await self.t.member(team_id, to)
        if row is not None and not self.t.is_running(row.session_id):
            await self.decide(team_id, to, row.status, "continue", "supervisor sent follow-up mail")

    async def decisions(self, team_id: str, limit: int = 200) -> list[dict[str, Any]]:
        async with self.t.s.db.session() as s:
            q = (
                select(EventRow)
                .where(EventRow.aggregate_id == "server", EventRow.type == "team.decision")
                .order_by(EventRow.seq.desc())
                .limit(5000)
            )
            found = [(r, self.t.s.events.plain(r.data)) for r in (await s.execute(q)).scalars()]
        rows = [(r, d) for r, d in found if d.get("team_id") == team_id]  # plain(): encrypted columns
        return [{"seq": r.seq, "created_at": r.created_at, **d} for r, d in reversed(rows[:limit])]
