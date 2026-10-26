"""Daily budget ceilings (spec §25.9 L1007). No `litellm` here.

`BudgetTracker` keeps today's (UTC) spend per deployment / key-pool slot, provider and tag,
seeded from the usage ledger at startup (`seed`) and advanced in-process by the gateway as
each call reports usage (`add`). Before a deployment is picked the gateway asks
`exhausted(deployment, tags)`; an exhausted deployment is skipped and, when nothing is
left, the call fails with a typed `BUDGET` error (never retried on the same tier).
`provider_remaining_budget{provider}` is published as a gauge on every change.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from typing import Any

from auoreo.core import metrics_text
from auoreo.core import observability as obs
from auoreo.gateway.pipeline import provider_of

log = logging.getLogger(__name__)
METRIC = "provider_remaining_budget"
obs.METRICS.setdefault(METRIC, ("updown", frozenset({"provider"})))
metrics_text.HELP.setdefault(METRIC, "Daily USD budget left per provider (gateway.budgets).")


def _today() -> date:
    return datetime.now(UTC).date()


class BudgetTracker:
    def __init__(self, cfg: Any, today: Callable[[], date] = _today) -> None:
        self.cfg = cfg  # core.settings_i2.BudgetSettings
        self._today = today
        self.day = today()
        self.spend: dict[tuple[str, str], float] = {}
        self._published: dict[str, float] = {}

    @property
    def active(self) -> bool:
        c = self.cfg
        return bool(c.per_key_daily or c.provider_daily or c.tag_daily)

    def _roll(self) -> None:
        if (d := self._today()) != self.day:
            self.day, self.spend = d, {}
            self.publish()

    def add(
        self, deployment: str | None, cost: float | None, tags: Iterable[str] = (), slot: str | None = None
    ) -> None:
        if not cost or deployment is None:
            return
        self._roll()
        keys = [("key", deployment), ("provider", provider_of(deployment) or deployment)]
        keys += [("tag", t) for t in tags]
        if slot:
            keys.append(("key", slot))
        for k in keys:
            self.spend[k] = self.spend.get(k, 0.0) + float(cost)
        self.publish()

    def seed(self, rows: Iterable[tuple[str | None, str | None, float | None]]) -> None:
        """`(deployment, comma-joined tags, cost)` rows of today's usage ledger."""
        self.day, self.spend = self._today(), {}
        for dep, tags, cost in rows:
            self.add(dep, cost, [t for t in (tags or "").split(",") if t])
        self.publish()

    def spent(self, kind: str, key: str) -> float:
        self._roll()
        return self.spend.get((kind, key), 0.0)

    def exhausted(self, deployment: str, tags: Iterable[str] = (), slot: str | None = None) -> str | None:
        """The first ceiling `deployment` (with these request tags) has reached, else None."""
        if not self.active:
            return None
        provider = provider_of(deployment) or deployment
        checks: list[tuple[str, str, float | None]] = [
            ("key", deployment, self.cfg.per_key_daily.get(deployment)),
            ("provider", provider, self.cfg.provider_daily.get(provider)),
            *[("tag", t, self.cfg.tag_daily.get(t)) for t in tags],
        ]
        if slot:
            checks.append(("key", slot, self.cfg.per_key_daily.get(slot)))
        for kind, key, cap in checks:
            if cap is not None and self.spent(kind, key) >= cap:
                return f"{kind} {key} reached its daily budget of ${cap:g}"
        return None

    def remaining(self, provider: str) -> float | None:
        cap = self.cfg.provider_daily.get(provider)
        return None if cap is None else max(0.0, cap - self.spent("provider", provider))

    def publish(self) -> None:
        """Gauge semantics on an up/down counter: add the delta since the last publish."""
        for provider in self.cfg.provider_daily:
            left = self.remaining(provider) or 0.0
            delta = left - self._published.get(provider, 0.0)
            if delta:
                obs.count(METRIC, delta, provider=provider)
                self._published[provider] = left


async def seed_from_ledger(tracker: BudgetTracker, db: Any) -> int:
    """Load today's spend from the `usage` table; returns the number of rows read."""
    from sqlalchemy import select

    from auoreo.core.db import UsageRow

    start = datetime.combine(tracker._today(), datetime.min.time(), tzinfo=UTC)
    q = select(UsageRow.deployment, UsageRow.tags, UsageRow.cost_usd).where(
        UsageRow.created_at >= start, UsageRow.cost_usd.is_not(None)
    )
    async with db.session() as s:
        rows = [(d, t, c) for d, t, c in (await s.execute(q)).all()]
    tracker.seed(rows)
    return len(rows)


__all__ = ["METRIC", "BudgetTracker", "seed_from_ledger"]
