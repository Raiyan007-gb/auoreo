"""Usage attribution for delegated subagents (spec §25.15): every `usage` row of a child
session carries `{parent_session_id, subagent_id, depth, task_name}`.

`TeamsService` registers a child here when it spawns it (process-local, keyed by the child
session id); `UsageLedger.record` stamps the row from it. `by_subagent` is the fold behind
`auoreo stats --by-subagent` and `GET /api/usage?by=subagent`.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from auoreo.core.db import UsageRow, utcnow

if TYPE_CHECKING:
    from auoreo.core.db import Database

FIELDS = ("parent_session_id", "subagent_id", "depth", "task_name")
_REGISTRY: dict[str, dict[str, Any]] = {}


def register(
    session_id: str, *, parent_session_id: str, subagent_id: str, depth: int, task_name: str
) -> None:
    _REGISTRY[session_id] = {
        "parent_session_id": parent_session_id,
        "subagent_id": subagent_id,
        "depth": depth,
        "task_name": task_name[:64],
    }


def lookup(session_id: str) -> dict[str, Any]:
    """The attribution columns for a usage row of `session_id` ({} for a top-level session)."""
    return dict(_REGISTRY.get(session_id, {}))


def forget(session_id: str) -> None:
    _REGISTRY.pop(session_id, None)


async def by_subagent(db: Database, days: int = 7, parent: str | None = None) -> list[dict[str, Any]]:
    """Calls/tokens/cost grouped by subagent (rows without one are the `(main)` bucket)."""
    since = utcnow() - timedelta(days=max(days, 0))
    q = (
        select(
            UsageRow.subagent_id,
            func.max(UsageRow.task_name),
            func.max(UsageRow.parent_session_id),
            func.max(UsageRow.depth),
            func.count(),
            func.coalesce(func.sum(UsageRow.prompt_tokens), 0),
            func.coalesce(func.sum(UsageRow.completion_tokens), 0),
            func.coalesce(func.sum(UsageRow.cost_usd), 0.0),
        )
        .where(UsageRow.created_at >= since)
        .group_by(UsageRow.subagent_id)
        .order_by(func.sum(UsageRow.cost_usd).desc(), UsageRow.subagent_id)
    )
    if parent is not None:
        q = q.where(UsageRow.parent_session_id == parent)
    async with db.session() as s:
        rows = (await s.execute(q)).all()
    return [
        {
            "subagent_id": sid or "(main)",
            "task_name": name or "",
            "parent_session_id": par or "",
            "depth": int(depth or 0),
            "calls": int(n),
            "prompt_tokens": int(p),
            "completion_tokens": int(c),
            "cost_usd": float(cost or 0.0),
        }
        for sid, name, par, depth, n, p, c, cost in rows
    ]
