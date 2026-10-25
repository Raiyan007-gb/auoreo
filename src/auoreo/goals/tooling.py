"""Helpers shared by `tools/{get,create,update}_goal/handler.py`."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import TYPE_CHECKING

from auoreo.goals.service import GoalError, goals_of
from auoreo.tools.context import ToolContext, ToolError

if TYPE_CHECKING:
    from auoreo.core.db import GoalRow
    from auoreo.goals.service import GoalsService


def goals_for(ctx: ToolContext) -> GoalsService:
    svc = ctx.services.get("services")
    if svc is None:
        raise ToolError("goals are not available (no services attached)")
    return goals_of(svc)


def fmt(row: GoalRow, *, long: bool = False) -> str:
    extra = f" → goal #{row.absorbed_into}" if row.absorbed_into else ""
    line = f"#{row.id} [{row.status}{extra}] {row.title} ({row.progress:.0%})"
    if not long:
        return line
    evidence = ", ".join(row.evidence_session_ids or []) or "none"
    desc = f"\n{row.description}" if row.description else ""
    return f"{line}{desc}\nevidence sessions: {evidence}"


async def attempt[T](aw: Awaitable[T]) -> T:
    try:
        return await aw
    except GoalError as exc:
        raise ToolError(str(exc)) from exc
