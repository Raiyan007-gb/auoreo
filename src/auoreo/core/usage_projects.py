"""G5a: usage grouped by project (the working directory a session was created in).

`session.created` events carry `cwd` (G5a); a forked/child session without one inherits its
parent's; anything older is grouped under `(unknown)`."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from auoreo.core.db import Database, EventRow, SessionRow
from auoreo.core.usage import UsageLedger

UNKNOWN = "(unknown)"


async def session_projects(db: Database) -> dict[str, str]:
    async with db.session() as s:
        created = (
            await s.execute(
                select(EventRow.aggregate_id, EventRow.data).where(EventRow.type == "session.created")
            )
        ).all()
        parents = dict((await s.execute(select(SessionRow.id, SessionRow.parent_id))).all())
    cwd = {str(agg): str(data.get("cwd")) for agg, data in created if data.get("cwd")}
    for sid, parent in parents.items():
        seen: set[str] = set()
        cur = parent
        while sid not in cwd and cur and cur not in seen:
            seen.add(cur)
            if cur in cwd:
                cwd[sid] = cwd[cur]
            cur = parents.get(cur)
    return cwd


async def by_project(db: Database, days: int = 7) -> list[dict[str, Any]]:
    summary = await UsageLedger(db).summary(days)
    projects = await session_projects(db)
    totals: dict[str, dict[str, Any]] = {}
    for row in summary["by_session"]:
        key = projects.get(row["key"], UNKNOWN)
        agg = totals.setdefault(
            key,
            {
                "key": key,
                "sessions": 0,
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost_usd": 0.0,
            },
        )
        agg["sessions"] += 1
        for field in ("calls", "prompt_tokens", "completion_tokens", "cost_usd"):
            agg[field] += row[field]
    return sorted(totals.values(), key=lambda r: (-r["cost_usd"], -r["calls"], r["key"]))
