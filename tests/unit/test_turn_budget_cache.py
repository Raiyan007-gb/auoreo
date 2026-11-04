"""Cache tokens in the per-turn budget and `turn.summary` (M7 W1a step 4)."""

from __future__ import annotations

import pytest

from auoreo.core.services import Services
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.runtime.budget import IterationBudget, TurnBudget


def _budget() -> TurnBudget:
    return TurnBudget(IterationBudget(10))


def test_record_accumulates_cache_tokens_and_summary_reports_the_ratio() -> None:
    b = _budget()
    b.record(1000, 10, 1, cached=0, cache_write=900)
    b.record(1100, 10, 1, cached=900, cache_write=100)
    s = b.summary("completed")
    assert (s["cached_tokens"], s["cache_write_tokens"]) == (900, 1000)
    assert s["cache_hit_ratio"] == pytest.approx(900 / 2100)
    assert s["prompt_tokens"] == 2100


def test_summary_without_cache_activity_is_zero_not_missing() -> None:
    b = _budget()
    b.record(100, 5)
    s = b.summary("completed")
    assert s["cached_tokens"] == 0 and s["cache_write_tokens"] == 0 and s["cache_hit_ratio"] == 0.0
    assert _budget().summary("completed")["cache_hit_ratio"] == 0.0  # no calls at all


async def _summary(services: Services, text: str) -> dict[str, object]:
    sess = await services.create_session()
    sub = services.bus.subscribe(frozenset({f"session:{sess.id}"}))
    await services.coordinator.submit(sess.id, text, interactive=False)
    await services.coordinator.wait(sess.id, 15)
    events = []
    while not sub.queue.empty():
        events.append(sub.queue.get_nowait())
    return next(e for e in events if e.type == "turn.summary").data


async def test_turn_summary_carries_the_cache_split_across_steps(
    services: Services, gateway: FakeGateway
) -> None:
    gateway.queue(
        Scripted(tool_calls=[("glob", {"pattern": "*"})], cache_write_tokens=50),
        Scripted(text="done", cached_tokens=30),
    )
    summary = await _summary(services, "look")
    assert summary["cached_tokens"] == 30 and summary["cache_write_tokens"] == 50
    assert summary["cache_hit_ratio"] == pytest.approx(30 / int(summary["prompt_tokens"]))  # type: ignore[call-overload]


async def test_length_recovery_keeps_the_cached_counts(services: Services, gateway: FakeGateway) -> None:
    services.settings.loop.otk_escalate = False
    gateway.queue(
        Scripted(text="part", finish="length", cached_tokens=10),
        Scripted(text=" rest", cached_tokens=20),
    )
    summary = await _summary(services, "write")
    assert summary["cached_tokens"] == 30 and int(summary["model_calls"]) >= 2  # type: ignore[call-overload]
