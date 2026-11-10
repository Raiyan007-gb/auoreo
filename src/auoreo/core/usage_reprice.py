"""Historical re-pricing of the usage ledger (`auoreo db recompute-costs`, M7 step 1.4).

Rows written before `m7.token.cached_pricing` charged every cached token at the full input
rate. This re-prices them with the current price function. Rules:

* only ESTIMATED rows that carry a cache split (`cached_tokens` or `cache_write_tokens` > 0);
  provider-reported costs and rows without a split are never touched;
* a row the price function cannot price now (None, or an error) keeps its stored cost;
* no marker is written. The price is a pure function of the row, so a second run finds
  nothing to change. (The design rules out a `price:cached` tag: `tags` is a bounded column
  that feeds the budget tag ceilings.) The consequence: a row is priced at TODAY's table and
  model configuration, not at the one in force when it was written;
* never called at startup; only the CLI command runs it.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select

from auoreo.core.db import Database, UsageRow, utcnow
from auoreo.core.usage import PriceFn

BATCH = 500


@dataclass
class RepriceReport:
    days: int | None
    dry_run: bool
    scanned: int = 0  # estimated rows with a cache split in the window
    repriced: int = 0  # rows whose cost changed (would change, with dry_run)
    unchanged: int = 0  # already at the cache-aware price
    unpriced: int = 0  # no price available now: stored cost kept
    old_total: float = 0.0  # USD over the priced rows, before
    new_total: float = 0.0  # USD over the priced rows, after

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _price(price: PriceFn, row: UsageRow) -> float | None:
    split = {"cached_tokens": row.cached_tokens, "cache_write_tokens": row.cache_write_tokens}
    try:
        return price(
            row.model, row.prompt_tokens, row.completion_tokens, **{k: v for k, v in split.items() if v}
        )
    except Exception:  # an unknown model or a broken table must not abort the run
        return None


def _same(old: float | None, new: float) -> bool:
    return old is not None and math.isclose(old, new, rel_tol=1e-9, abs_tol=1e-12)


async def recompute_costs(
    db: Database, price: PriceFn, *, days: int | None = None, dry_run: bool = False
) -> RepriceReport:
    """Re-price the window (`days` back from now; None = every row). One commit per batch."""
    report = RepriceReport(days=days, dry_run=dry_run)
    where = [
        UsageRow.estimated.is_(True),
        or_(UsageRow.cached_tokens > 0, UsageRow.cache_write_tokens > 0),
    ]
    if days is not None:
        where.append(UsageRow.created_at >= utcnow() - timedelta(days=days))
    last_id = 0
    async with db.session() as s:
        while True:
            q = select(UsageRow).where(UsageRow.id > last_id, *where).order_by(UsageRow.id).limit(BATCH)
            rows = list((await s.execute(q)).scalars())
            if not rows:
                break
            last_id = rows[-1].id
            for row in rows:
                report.scanned += 1
                new = _price(price, row)
                if new is None:
                    report.unpriced += 1
                    continue
                report.old_total += row.cost_usd or 0.0
                report.new_total += new
                if _same(row.cost_usd, new):
                    report.unchanged += 1
                    continue
                report.repriced += 1
                if not dry_run:
                    row.cost_usd = new
            if dry_run:
                s.expunge_all()
            else:
                await s.commit()
    return report


__all__ = ["RepriceReport", "recompute_costs"]
