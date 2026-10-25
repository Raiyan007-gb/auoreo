"""Helpers shared by the teams/kanban tool handlers in `tools/<name>/handler.py`."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import TYPE_CHECKING

from auoreo.tools.context import ToolContext, ToolError

if TYPE_CHECKING:
    from auoreo.core.db import TeamRow
    from auoreo.teams.service import TeamsService


def teams_for(ctx: ToolContext) -> TeamsService:
    svc = ctx.services.get("services")
    teams = svc.ext.get("teams") if svc is not None else None
    if teams is None:
        raise ToolError("teams are not available (the teams extension is not running)")
    return teams  # type: ignore[no-any-return]


async def my_team(ctx: ToolContext) -> tuple[TeamsService, TeamRow, str]:
    """(service, the caller's team, the caller's name in it). Errors if not in a team."""
    teams = teams_for(ctx)
    team = await teams.team_of(ctx.session_id)
    if team is None:
        raise ToolError("this session is not in a team yet; delegate a task or call team_create first")
    return teams, team, await attempt(teams.name_in_team(team, ctx.session_id))


async def attempt[T](aw: Awaitable[T]) -> T:
    """Await a teams/kanban call, turning its refusals into model-visible tool errors."""
    try:
        return await aw
    except ValueError as exc:  # TeamsError / KanbanError
        raise ToolError(str(exc)) from exc
