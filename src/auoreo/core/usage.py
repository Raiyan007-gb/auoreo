"""Usage ledger (spec §20.2): one `usage` row per model call, with cost when known.

Costs reported by the provider are stored as-is; otherwise the gateway's price table is
used and the row is marked `estimated`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select

from auoreo.core.db import Database, SessionRow, UsageRow, utcnow
from auoreo.gateway.types import Usage

PriceFn = Callable[[str, int, int], float | None]


class UsageLedger:
    def __init__(self, db: Database, price: PriceFn | None = None) -> None:
        self.db = db
        self.price = price

    async def record(self, session_id: str, run_id: str, model: str, usage: Usage) -> UsageRow:
        cost, estimated = usage.cost_usd, usage.estimated
        if cost is None and self.price is not None:
            try:
                cost = self.price(model, usage.prompt_tokens, usage.completion_tokens)
            except Exception:  # pricing must never fail a turn
                cost = None
            estimated = True
        row = UsageRow(
            session_id=session_id,
            run_id=run_id,
            model=model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cost_usd=cost,
            estimated=estimated,
        )
        async with self.db.session() as s:
            s.add(row)
            await s.commit()
        return row

    async def by_session(
        self, session_id: str | None = None, limit: int = 200, *, children: bool = False
    ) -> list[UsageRow]:
        """Newest first. `children=True` rolls in every descendant session (delegated
        subagents, workflow steps) of `session_id`."""
        async with self.db.session() as s:
            q = select(UsageRow).order_by(UsageRow.id.desc()).limit(limit)
            if session_id is not None:
                ids = await self.session_tree(session_id) if children else [session_id]
                q = q.where(UsageRow.session_id.in_(ids))
            return list((await s.execute(q)).scalars())

    async def session_tree(self, session_id: str, max_depth: int = 16) -> list[str]:
        """`session_id` plus all of its descendants via `sessions.parent_id`."""
        ids, frontier = [session_id], [session_id]
        async with self.db.session() as s:
            for _ in range(max_depth):
                q = select(SessionRow.id).where(SessionRow.parent_id.in_(frontier))
                frontier = [i for i in (await s.execute(q)).scalars() if i not in ids]
                if not frontier:
                    break
                ids.extend(frontier)
        return ids

    async def totals(self, session_ids: list[str]) -> dict[str, Any]:
        """Summed tokens/cost over the given sessions."""
        q = select(
            func.count(),
            func.coalesce(func.sum(UsageRow.prompt_tokens), 0),
            func.coalesce(func.sum(UsageRow.completion_tokens), 0),
            func.coalesce(func.sum(UsageRow.cost_usd), 0.0),
        ).where(UsageRow.session_id.in_(session_ids))
        async with self.db.session() as s:
            n, p, c, cost = (await s.execute(q)).one()
        return {
            "calls": int(n),
            "prompt_tokens": int(p),
            "completion_tokens": int(c),
            "cost_usd": float(cost or 0.0),
        }

    async def session_cost(self, session_id: str) -> float:
        async with self.db.session() as s:
            q = select(func.coalesce(func.sum(UsageRow.cost_usd), 0.0)).where(
                UsageRow.session_id == session_id
            )
            return float((await s.execute(q)).scalar_one() or 0.0)

    async def over_budget(self, session_id: str, cap: float | None) -> bool:
        return cap is not None and await self.session_cost(session_id) >= cap

    async def summary(self, days: int = 7) -> dict[str, Any]:
        since = utcnow() - timedelta(days=max(days, 0))

        async def grouped(col: Any) -> list[dict[str, Any]]:
            q = (
                select(
                    col,
                    func.count(),
                    func.coalesce(func.sum(UsageRow.prompt_tokens), 0),
                    func.coalesce(func.sum(UsageRow.completion_tokens), 0),
                    func.coalesce(func.sum(UsageRow.cost_usd), 0.0),
                )
                .where(UsageRow.created_at >= since)
                .group_by(col)
                .order_by(func.sum(UsageRow.cost_usd).desc(), col)
            )
            async with self.db.session() as s:
                rows = (await s.execute(q)).all()
            return [
                {
                    "key": str(k),
                    "calls": int(n),
                    "prompt_tokens": int(p),
                    "completion_tokens": int(c),
                    "cost_usd": float(cost or 0.0),
                }
                for k, n, p, c, cost in rows
            ]

        by_model = await grouped(UsageRow.model)
        by_session = await grouped(UsageRow.session_id)
        total = {
            "key": "total",
            "calls": sum(r["calls"] for r in by_model),
            "prompt_tokens": sum(r["prompt_tokens"] for r in by_model),
            "completion_tokens": sum(r["completion_tokens"] for r in by_model),
            "cost_usd": sum(r["cost_usd"] for r in by_model),
        }
        return {"days": days, "total": total, "by_model": by_model, "by_session": by_session}


__all__ = ["UsageLedger"]
