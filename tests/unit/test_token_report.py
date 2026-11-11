"""`auoreo stats --token-economy` (M7 W1 Wave 2): the measurement gates, read-only.

Seeds `usage` rows, events, messages and bandit outcomes on a `:memory:` database and checks
each gate's value and verdict, including `insufficient data` below the stated minimum.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from typer.testing import CliRunner

from auoreo.core import token_report as tr
from auoreo.core.db import BanditOutcomeRow, MessageRow, UsageRow, utcnow
from auoreo.core.services import Services

CLAUDE = "anthropic/claude-test"
OTHER = "openai/gpt-test"


def price(model: str, p: int, c: int, /, *, cached_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    """Cheap model at a third of the default rate; reads 0.1x, writes 1.25x."""
    rate = 1e-6 if model == "cheap" else 3e-6
    plain = p - cached_tokens - cache_write_tokens
    return rate * (plain + 0.1 * cached_tokens + 1.25 * cache_write_tokens + 5 * c)


async def seed_turn(
    svc: Services,
    sid: str,
    run: str,
    *,
    calls: list[tuple[int, int]],
    model: str = CLAUDE,
    cost: float = 0.01,
    text: str = "what does HTTP 409 mean?",
    tools: int = 0,
    agent: str = "build",
    at: datetime | None = None,
    gap_s: float = 1.0,
) -> None:
    """One main turn: `calls` = (prompt, cached) per usage row."""
    at = at or utcnow()
    async with svc.db.session() as s:
        for i, (prompt, cached) in enumerate(calls):
            s.add(
                UsageRow(
                    session_id=sid,
                    run_id=run,
                    model=model,
                    prompt_tokens=prompt,
                    completion_tokens=100,
                    cached_tokens=cached,
                    cost_usd=cost,
                    estimated=True,
                    outcome="ok",
                    created_at=at + timedelta(seconds=gap_s * i),
                )
            )
        await s.commit()
    await svc.emit(sid, "run.started", {"run_id": run, "agent": agent, "model": model})
    await svc.emit(sid, "message.created", {"run_id": run, "role": "user", "content": text})
    summary = {"run_id": run, "status": "completed", "steps": len(calls), "tools": tools}
    await svc.emit(sid, "turn.summary", summary)


def gate(report: tr.Report, gid: str) -> tr.Gate:
    return next(g for g in report.gates if g.id == gid)


async def build(svc: Services, days: int = 14) -> tr.Report:
    return await tr.build(svc.db, svc.settings, price, days)


async def test_an_empty_database_reports_insufficient_data_everywhere(services: Services) -> None:
    report = await build(services)
    assert [g.id for g in report.gates] == ["B1", "B2", "T1", "R1", "R2", "R3", "M1", "M2", "M3", "C1"]
    assert {g.verdict for g in report.gates} == {tr.INSUFFICIENT}
    assert report.flags["cache_layout"] == "legacy"


async def test_the_report_writes_nothing(services: Services) -> None:
    sid = (await services.create_session()).id
    await seed_turn(services, sid, "r1", calls=[(1000, 0), (1100, 900)])
    async with services.db.session() as s:
        before = (await s.execute(select(func.count()).select_from(UsageRow))).scalar()
    await build(services)
    async with services.db.session() as s:
        assert (await s.execute(select(func.count()).select_from(UsageRow))).scalar() == before


@pytest.mark.parametrize(("changes", "verdict"), [(2, tr.PASS), (3, tr.FAIL)])
async def test_b1_prefix_changes_per_100_turns(services: Services, changes: int, verdict: str) -> None:
    sid = (await services.create_session()).id
    for i in range(100):
        await seed_turn(services, sid, f"r{i}", calls=[(1000, 0)])
    for _ in range(changes):
        await services.emit(sid, "cache.prefix_changed", {"run_id": "r1"})
    b1 = gate(await build(services), "B1")
    assert (b1.value, b1.samples, b1.verdict) == (pytest.approx(changes), 100, verdict)


async def test_b1_below_the_minimum_is_insufficient(services: Services) -> None:
    sid = (await services.create_session()).id
    for i in range(99):
        await seed_turn(services, sid, f"r{i}", calls=[(1000, 0)])
    assert gate(await build(services), "B1").verdict == tr.INSUFFICIENT


async def test_b2_median_tail_hit_of_anthropic_calls_only(services: Services) -> None:
    sid = (await services.create_session()).id
    for i in range(50):  # first call cold, tail call 80% cached
        await seed_turn(services, sid, f"r{i}", calls=[(1000, 0), (1000, 800)])
    for i in range(20):  # other families are not counted
        await seed_turn(services, sid, f"o{i}", calls=[(1000, 0), (1000, 0)], model=OTHER)
    b2 = gate(await build(services), "B2")
    assert (b2.value, b2.samples, b2.verdict) == (pytest.approx(0.8), 50, tr.PASS)


async def test_b2_ignores_subagent_sessions(services: Services) -> None:
    parent = (await services.create_session()).id
    child = (await services.create_session(parent_id=parent)).id
    await seed_turn(services, child, "c1", calls=[(1000, 0), (1000, 1000)])
    assert gate(await build(services), "B2").samples == 0


async def test_t1_first_calls_within_the_ttl_against_the_expected_prefix(services: Services) -> None:
    sid = (await services.create_session()).id
    start = utcnow() - timedelta(hours=2)
    for i in range(51):  # 60 s apart (< 5 min TTL); the previous prompt is 1000 of 2000 → expect 0.5
        await seed_turn(services, sid, f"r{i:03d}", calls=[(2000, 300)], at=start + timedelta(seconds=60 * i))
    t1 = gate(await build(services), "T1")
    assert t1.samples == 50
    assert t1.detail["median_measured"] == pytest.approx(0.15)
    assert t1.detail["median_expected"] == pytest.approx(1.0)
    assert t1.verdict == tr.FAIL  # 0.15 < 0.8 of expected: reopen the previous-turn breakpoint


async def test_t1_skips_turns_after_the_ttl(services: Services) -> None:
    sid = (await services.create_session()).id
    start = utcnow() - timedelta(days=1)
    for i in range(60):  # 10 minutes apart: the 5-minute cache has expired
        await seed_turn(services, sid, f"r{i:03d}", calls=[(2000, 0)], at=start + timedelta(minutes=10 * i))
    assert gate(await build(services), "T1").samples == 0


async def test_route_gates_on_eligible_turns(services: Services) -> None:
    services.settings.models["small"].models[:] = ["cheap"]
    sid = (await services.create_session()).id
    for i in range(200):  # eligible: short question, no override, small history
        await seed_turn(services, sid, f"e{i}", calls=[(1000, 900)], tools=1 if i < 50 else 0)
    for i in range(10):  # not eligible: long text
        await seed_turn(services, sid, f"n{i}", calls=[(1000, 900)], text="x" * 500, cost=0.2)
    report = await build(services)
    r1, r2, r3 = gate(report, "R1"), gate(report, "R2"), gate(report, "R3")
    assert r1.samples == 200 and r1.value == pytest.approx(2.0 / 4.0) and r1.verdict == tr.PASS
    assert r2.value == pytest.approx(0.25) and r2.verdict == tr.PASS
    cold = price("cheap", 1000, 100, cache_write_tokens=1000)
    warm = price("cheap", 1000, 100, cached_tokens=900)
    assert r3.value == pytest.approx(1 - (200 * cold + 0.25 * 2.0) / 2.0)
    assert r3.detail["warm"] == pytest.approx(1 - (200 * warm + 0.25 * 2.0) / 2.0)
    assert r3.detail["cheap_model"] == "cheap"


async def test_route_excludes_overrides_planner_and_corrections(services: Services) -> None:
    pinned = (await services.create_session(model="default")).id
    free = (await services.create_session()).id
    await seed_turn(services, pinned, "p1", calls=[(1000, 0)])
    await seed_turn(services, free, "p2", calls=[(1000, 0)], agent="planner")
    await seed_turn(services, free, "p3", calls=[(1000, 0)], text="no, that's wrong")
    await seed_turn(services, free, "p4", calls=[(1000, 0)])
    assert gate(await build(services), "R1").samples == 1


async def test_route_history_is_estimated_from_messages(services: Services) -> None:
    sid = (await services.create_session()).id
    async with services.db.session() as s:
        s.add(MessageRow(session_id=sid, idx=0, message={"role": "user", "content": "y" * 20_000}))
        s.add(MessageRow(session_id=sid, idx=1, message={"role": "assistant", "content": "ok"}))
        s.add(MessageRow(session_id=sid, idx=2, message={"role": "user", "content": "short?"}))
        await s.commit()
    await seed_turn(services, sid, "h1", calls=[(1000, 0)], text="short?")
    assert gate(await build(services), "R1").samples == 0  # 5000 history tokens > 4000


def _tool_pair(i: int, name: str, size: int) -> list[dict[str, Any]]:
    call = {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": "{}"}}
    return [
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "tool_call_id": f"c{i}", "content": "x" * size},
    ]


async def _mask_session(svc: Services, *, compacted: bool = False, calls_after: int = 3) -> str:
    sid = (await svc.create_session()).id
    history: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    if compacted:
        history.append({"role": "user", "content": '<compaction-summary id="x">s</compaction-summary>'})
    for i in range(16):
        history += _tool_pair(i, "read", 10_000)
    early = utcnow() - timedelta(hours=1)
    async with svc.db.session() as s:
        for idx, m in enumerate(history):
            s.add(MessageRow(session_id=sid, idx=idx, message=m, created_at=early))
        await s.commit()
    await seed_turn(svc, sid, f"{sid}-r", calls=[(40_000, 0)] * calls_after, text="x" * 1000, model=OTHER)
    return sid


async def test_m1_maskable_share_and_compacted_sessions_are_skipped(services: Services) -> None:
    for _ in range(30):
        await _mask_session(services)
    await _mask_session(services, compacted=True)
    report = await build(services)
    m1 = gate(report, "M1")
    assert m1.samples == 30 and report.notes["sessions_skipped_compacted"] == 1
    # 16 results of 10k chars: the first sits in the protected head, the last 10 are kept
    assert m1.value == pytest.approx(5 * (10_000 - 240) / 4 / (160_000 // 4), rel=0.01)
    assert m1.verdict == tr.PASS


async def test_m2_share_of_summarizer_compactions(services: Services) -> None:
    sid = (await services.create_session()).id
    await seed_turn(services, sid, "r1", calls=[(1000, 0)])
    for source, trigger in (("summary", "pressure"), ("summary", "manual"), ("prune", "pressure")):
        await services.emit(sid, "compaction.completed", {"source": source, "trigger": trigger})
    m2 = gate(await build(services), "M2")
    assert (m2.value, m2.samples, m2.verdict) == (pytest.approx(1 / 3), 3, tr.PASS)


def test_break_even_matches_the_design_example() -> None:
    """h = 0.9, S = M/2, Anthropic 5m multipliers: N* is about 2.7 steps."""
    assert tr.break_even(1000, 500, 0.9, 0.1, 1.25) == pytest.approx(2.72, abs=0.01)
    assert tr.break_even(1000, 500, 0.0, 0.1, 1.25) == 0.0  # a cold cache: masking is free
    assert tr.break_even(1000, 500, 0.9, 1.0, 1.0) == 0.0  # no caching: nothing to lose


async def test_m3_calls_after_the_maskable_threshold(services: Services) -> None:
    sid = (await services.create_session()).id
    for i in range(50):  # B2 = 0.9
        await seed_turn(services, sid, f"b{i}", calls=[(1000, 0), (1000, 900)])
    for _ in range(30):
        await _mask_session(services, calls_after=40)
    m3 = gate(await build(services), "M3")
    assert m3.samples == 30 and m3.value == 40
    assert m3.detail["h"] == pytest.approx(0.9)
    assert (m3.detail["read_multiplier"], m3.detail["write_multiplier"]) == (0.1, 1.25)
    assert m3.verdict == tr.PASS


async def test_m3_multipliers_are_configurable(services: Services) -> None:
    services.settings.m7.token.mask.cache_read_multiplier = 0.05
    services.settings.gateway_tuning.cache_ttl = "1h"
    m3 = gate(await build(services), "M3")
    assert (m3.detail["read_multiplier"], m3.detail["write_multiplier"]) == (0.05, 2.0)


async def _outcomes(svc: Services, key: str, n: int, hit: int, ratios: list[float]) -> None:
    async with svc.db.session() as s:
        for i in range(n):
            signals = {"cache_hit": hit, "cache_ratio": ratios[i % len(ratios)]}
            s.add(
                BanditOutcomeRow(
                    session_id="s", run_id=f"{key}{i}", context_key=key, arm_key="a", signals=signals
                )
            )
        await s.commit()


async def test_c1_needs_a_saturated_binary_signal_and_a_spread_ratio(services: Services) -> None:
    await _outcomes(services, "agent:a", 200, 1, [0.2, 0.9])  # share 1.0, stdev 0.35
    await _outcomes(services, "agent:b", 200, 1, [0.9])  # no spread
    await _outcomes(services, "agent:c", 50, 1, [0.1, 0.9])  # too few
    c1 = gate(await build(services), "C1")
    assert c1.detail["contexts"] == ["agent:a"] and c1.samples == 2 and c1.verdict == tr.PASS
    services.settings.m7.token.cached_pricing = True
    assert gate(await build(services), "C1").verdict == tr.FAIL


# ------------------------------------------------------------------ CLI
runner = CliRunner()


def test_cli_prints_every_gate_as_json(monkeypatch: pytest.MonkeyPatch, workdir: Path) -> None:
    from auoreo.cli.app import app

    monkeypatch.setenv("AUOREO_GATEWAY", "fake")
    monkeypatch.setenv("AUOREO_PORT", "9")
    monkeypatch.chdir(workdir)
    r = runner.invoke(app, ["stats", "--token-economy", "--days", "14", "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert data["days"] == 14 and len(data["gates"]) == 10
    assert all(g["verdict"] == tr.INSUFFICIENT for g in data["gates"])
    table = runner.invoke(app, ["stats", "--token-economy"])
    assert table.exit_code == 0 and "token economy gates" in table.output and "main_turns 0" in table.output
