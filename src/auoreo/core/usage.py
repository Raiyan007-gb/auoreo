"""Usage ledger (spec §20.2): one `usage` row per model call, with cost when known.

Costs reported by the provider are stored as-is; otherwise the gateway's price table is
used and the row is marked `estimated`.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, timedelta
from typing import Any, TypeVar

from sqlalchemy import func, select

from auoreo.core import observability as obs
from auoreo.core import usage_attrib
from auoreo.core.db import Database, SessionRow, UsageRow, utcnow
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.types import Usage

PriceFn = Callable[[str, int, int], float | None]
T = TypeVar("T")


class UsageLedger:
    def __init__(self, db: Database, price: PriceFn | None = None) -> None:
        self.db = db
        self.price = price

    async def record(
        self,
        session_id: str,
        run_id: str,
        model: str,
        usage: Usage,
        *,
        latency_ms: int | None = None,
        outcome: str | None = None,
    ) -> UsageRow:
        """`latency_ms`/`outcome` (`ok` or a GatewayError code) are set for main-turn calls."""
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
            completion_tokens=usage.completion_tokens,  # includes reasoning tokens
            cached_tokens=usage.cached_tokens,
            cost_usd=cost,
            estimated=estimated,
            latency_ms=latency_ms,
            outcome=outcome,
            **usage_attrib.lookup(session_id),  # --- I3: parent/subagent/depth (spec §25.15)
        )
        async with self.db.session() as s:
            s.add(row)
            await s.commit()
        obs.record_usage(model, usage.prompt_tokens, usage.completion_tokens, cost)  # --- W0-D
        return row

    def meter(self, session_id: str, run_id: str, model: str) -> UsageMeter:
        return UsageMeter(self, session_id, run_id, model)

    async def by_run(self, session_id: str, run_id: str) -> list[UsageRow]:
        async with self.db.session() as s:
            q = select(UsageRow).where(UsageRow.session_id == session_id, UsageRow.run_id == run_id)
            return list((await s.execute(q.order_by(UsageRow.id))).scalars())

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

    async def root_of(self, session_id: str, max_depth: int = 16) -> str:
        """The top-level ancestor of `session_id` via `sessions.parent_id` (itself if none)."""
        current = session_id
        async with self.db.session() as s:
            for _ in range(max_depth):
                row = await s.get(SessionRow, current)
                if row is None or not row.parent_id:
                    break
                current = row.parent_id
        return current

    async def tree_cost(self, session_id: str) -> float:
        """Spend of `session_id`'s root and every descendant (spec §25.15: child spend counts
        toward the parent's `max_cost_usd`)."""
        tree = await self.session_tree(await self.root_of(session_id))
        return float((await self.totals(tree))["cost_usd"])

    async def over_budget(self, session_id: str, cap: float | None) -> bool:
        return cap is not None and await self.tree_cost(session_id) >= cap

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

    async def series(self, days: int = 7, bucket: str = "day") -> list[dict[str, Any]]:
        """Tokens/cost per hour or day (UTC), oldest first, empty buckets filled with zeros."""
        now = utcnow()
        since = now - timedelta(days=max(days, 0) or 1)
        q = select(
            UsageRow.created_at, UsageRow.prompt_tokens, UsageRow.completion_tokens, UsageRow.cost_usd
        ).where(UsageRow.created_at >= since)
        async with self.db.session() as s:
            rows = (await s.execute(q)).all()
        step = timedelta(hours=1) if bucket == "hour" else timedelta(days=1)

        def floor(ts: Any) -> Any:
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            ts = ts.replace(minute=0, second=0, microsecond=0)
            return ts if bucket == "hour" else ts.replace(hour=0)

        out: dict[Any, dict[str, Any]] = {}
        cur = floor(since)
        while cur <= now:
            out[cur] = {
                "bucket": cur,
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cost_usd": 0.0,
            }
            cur += step
        for ts, p, c, cost in rows:
            pt = out.setdefault(
                floor(ts),
                {
                    "bucket": floor(ts),
                    "calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cost_usd": 0.0,
                },
            )
            pt["calls"] += 1
            pt["prompt_tokens"] += int(p or 0)
            pt["completion_tokens"] += int(c or 0)
            pt["cost_usd"] += float(cost or 0.0)
        return [out[k] for k in sorted(out)]


class UsageMeter:
    """Times one main-turn gateway call. `record` writes the usage row with the wall time
    since the call opened and `outcome="ok"`; `failed` writes a zero-token row carrying the
    GatewayError code when the call died before reporting usage."""

    def __init__(self, ledger: UsageLedger, session_id: str, run_id: str, model: str) -> None:
        self.ledger, self.session_id, self.run_id, self.model = ledger, session_id, run_id, model
        self.started = time.monotonic()
        self.recorded = False

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)

    async def record(self, usage: Usage) -> UsageRow:
        self.recorded = True
        return await self.ledger.record(
            self.session_id, self.run_id, self.model, usage, latency_ms=self.elapsed_ms(), outcome="ok"
        )

    async def wrap(self, stream: AsyncIterator[T]) -> AsyncIterator[T]:
        """Pass `stream` through; a GatewayError before usage was reported is metered."""
        try:
            async for item in stream:
                yield item
        except GatewayError as exc:
            await self.failed(exc.code)
            raise

    async def failed(self, code: str) -> None:
        if self.recorded:
            return
        self.recorded = True
        await self.ledger.record(
            self.session_id, self.run_id, self.model, Usage(), latency_ms=self.elapsed_ms(), outcome=code
        )


__all__ = ["UsageLedger", "UsageMeter"]
