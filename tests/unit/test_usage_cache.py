"""Cache accounting in the usage ledger (M7 W1a step 3): cached and cache-write tokens are
summed everywhere prompt and completion tokens are, and the hit ratio is defined once."""

from __future__ import annotations

from typing import Any

import pytest

from auoreo.core import metrics_text
from auoreo.core.services import Services
from auoreo.core.usage import UsageLedger, cache_hit_ratio
from auoreo.gateway.types import Usage


def test_cache_hit_ratio_is_cached_over_prompt_and_zero_without_prompt() -> None:
    assert cache_hit_ratio(0, 0) == 0.0
    assert cache_hit_ratio(750, 1000) == pytest.approx(0.75)
    assert cache_hit_ratio(2000, 1000) == 1.0  # clamped: a bad provider count never exceeds 100%


async def test_price_gets_the_cache_split_only_when_there_is_one(services: Services) -> None:
    calls: list[dict[str, Any]] = []

    def price(model: str, p: int, c: int, **cache: int) -> float | None:
        calls.append(cache)
        return 0.0

    ledger = UsageLedger(services.db, price)
    await ledger.record("s", "r1", "m", Usage(100, 10))
    await ledger.record("s", "r2", "m", Usage(100, 10, cached_tokens=60, cache_write_tokens=20))
    assert calls == [{}, {"cached_tokens": 60, "cache_write_tokens": 20}]


async def test_aggregates_sum_cached_and_cache_write_tokens(services: Services) -> None:
    ledger = UsageLedger(services.db)
    await ledger.record("s1", "r1", "m-a", Usage(1000, 10, cost_usd=0.1, cached_tokens=800), outcome="ok")
    await ledger.record(
        "s1", "r2", "m-a", Usage(1000, 10, cost_usd=0.1, cache_write_tokens=900), outcome="ok"
    )
    await ledger.record("s1", "r3", "m-a", Usage(500, 5, cost_usd=0.0), outcome=None)  # aux call
    totals = await ledger.totals(["s1"])
    assert totals["cached_tokens"] == 800 and totals["cache_write_tokens"] == 900
    assert totals["cache_hit_ratio"] == pytest.approx(0.4)  # 800 / 2000: the aux row is excluded
    summary = await ledger.summary(7)
    assert summary["total"]["cached_tokens"] == 800
    assert summary["by_model"][0]["cache_write_tokens"] == 900
    assert summary["by_model"][0]["cache_hit_ratio"] == pytest.approx(0.4)
    series = await ledger.series(1, "day")
    assert sum(b["cached_tokens"] for b in series) == 800
    assert sum(b["cache_write_tokens"] for b in series) == 900


async def test_cache_tokens_reach_the_metrics_text(services: Services) -> None:
    before = metrics_text.value("tokens_total", model="m-cache", type="cached")
    await UsageLedger(services.db).record("s", "r", "m-cache", Usage(100, 1, cost_usd=0.0, cached_tokens=70))
    assert metrics_text.value("tokens_total", model="m-cache", type="cached") == before + 70
    assert "cached" in metrics_text.HELP["tokens_total"]
