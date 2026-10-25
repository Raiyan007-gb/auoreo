"""Which team a workflow step belongs to.

Multi-step workflows run each step in a child session that is not a team member. A step
joins a team when its workflow session is in one, or when an earlier step of the same run
created one (e.g. the swarm coordinator's delegations): the step is added as a member named
after its step id, so the board, mailbox and kanban tools see one shared team. A step whose
run has no team stays team-less (board tools then report "not in a team").
"""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select

from auoreo.core.db import SessionRow, TeamMemberRow, TeamRow

if TYPE_CHECKING:
    from auoreo.teams.service import TeamsService


def _member_name(title: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9_]+", "_", title.rsplit(":", 1)[-1].lower()).strip("_")[:56] or "step"
    name, n = base, 1
    while name in taken or name == "lead":
        n += 1
        name = f"{base}_{n}"
    return name if len(name) <= 64 else f"step_{uuid.uuid4().hex[:8]}"


async def workflow_team(teams: TeamsService, session_id: str) -> TeamRow | None:
    async with teams.join_lock:  # parallel tool calls of one step must join only once
        return await teams.team_of(session_id) or await _join(teams, session_id)


async def _join(teams: TeamsService, session_id: str) -> TeamRow | None:
    row = await teams.s.get_session(session_id)
    if row is None or not row.parent_id:
        return None
    parent = await teams.s.get_session(row.parent_id)
    wf = teams.s.harness.workflows.get(parent.workflow) if parent is not None and parent.workflow else None
    if parent is None or wf is None or len(wf.manifest.steps) < 2:
        return None
    team = await teams.team_of(parent.id)
    if team is None:
        async with teams.s.db.session() as s:
            q = select(SessionRow.id).where(SessionRow.parent_id == parent.id, SessionRow.id != session_id)
            siblings = list((await s.execute(q.order_by(SessionRow.created_at))).scalars())
        for sib in siblings:
            if (team := await teams.team_of(sib)) is not None:
                break
    if team is None:
        return None
    taken = {m.name for m in await teams.members(team.id)}
    async with teams.s.db.session() as s:
        s.add(
            TeamMemberRow(
                team_id=team.id,
                name=_member_name(row.title or "", taken),
                agent=row.agent,
                session_id=session_id,
                depth=await teams.depth(session_id),
                status="running",
            )
        )
        await s.commit()
    return team
