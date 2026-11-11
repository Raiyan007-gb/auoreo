"""`auoreo db recompute-costs [--days N] [--dry-run] [--all]` (M7 step 1.4 remainder, Wave 2 mark).

Estimated rows that carry a cache split are re-priced with the current, cache-aware price
function. Provider-reported costs, rows without cache tokens and rows the price table does not
know are left alone; a second run changes nothing; `--dry-run` writes nothing. There is no
marker on the row (the design rules out the `price:cached` tag and a new column needs a
migration): a completed run stores the highest scanned id as `usage_reprice_mark` in
`schema_meta` and the next run starts after it, unless `--all`.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, update
from typer.testing import CliRunner

from auoreo.cli.app import app
from auoreo.core import usage_reprice
from auoreo.core.db import Meta, UsageRow, utcnow
from auoreo.core.services import Services
from auoreo.core.usage import UsageLedger
from auoreo.gateway.types import Usage

runner = CliRunner()


def old_price(model: str, p: int, c: int, /, **_cache: int) -> float | None:
    """Every prompt token at the full input rate: how rows were priced before cached pricing."""
    return None if model == "unknown" else (p + c) * 1e-6


def new_price(
    model: str, p: int, c: int, /, *, cached_tokens: int = 0, cache_write_tokens: int = 0
) -> float | None:
    if model == "unknown":
        return None
    return (p + c) * 1e-6 - cached_tokens * 0.9e-6 + cache_write_tokens * 0.25e-6


async def _seed(svc: Services) -> None:
    ledger = UsageLedger(svc.db, old_price)
    await ledger.record("s", "r1", "m", Usage(1000, 100, cached_tokens=800))  # 1: re-priced down
    await ledger.record("s", "r2", "m", Usage(1000, 100, cache_write_tokens=400))  # 2: re-priced up
    await ledger.record("s", "r3", "m", Usage(1000, 100))  # 3: no cache split
    await ledger.record("s", "r4", "m", Usage(1000, 100, cost_usd=0.5, cached_tokens=800))  # 4: provider cost
    await ledger.record("s", "r5", "unknown", Usage(1000, 100, cached_tokens=800))  # 5: no price
    await ledger.record("s", "r6", "m", Usage(1000, 100, cached_tokens=500, tags=["team:a"]))  # 6: old row
    async with svc.db.session() as s:
        await s.execute(
            update(UsageRow).where(UsageRow.run_id == "r6").values(created_at=utcnow() - timedelta(days=40))
        )
        await s.commit()


async def _rows(svc: Services) -> dict[str, UsageRow]:
    async with svc.db.session() as s:
        return {r.run_id: r for r in (await s.execute(select(UsageRow))).scalars()}


async def test_reprices_only_estimated_rows_with_a_cache_split(services: Services) -> None:
    await _seed(services)
    before = await _rows(services)
    report = await usage_reprice.recompute_costs(services.db, new_price)
    after = await _rows(services)
    assert (report.scanned, report.repriced, report.unchanged, report.unpriced) == (4, 3, 0, 1)
    assert after["r1"].cost_usd == pytest.approx(1100e-6 - 800 * 0.9e-6)
    assert after["r2"].cost_usd == pytest.approx(1100e-6 + 400 * 0.25e-6)
    assert after["r6"].cost_usd == pytest.approx(1100e-6 - 500 * 0.9e-6)
    for run in ("r3", "r4", "r5"):
        assert after[run].cost_usd == before[run].cost_usd
    assert after["r4"].estimated is False and after["r5"].cost_usd is None
    assert report.old_total == pytest.approx(3 * 1100e-6)
    assert report.new_total == pytest.approx(sum(after[r].cost_usd or 0 for r in ("r1", "r2", "r6")))
    assert report.dry_run is False


async def test_rows_keep_their_tags_and_other_columns(services: Services) -> None:
    """No `price:cached` tag: `tags` feeds the budget tag ceilings and is a bounded column."""
    await _seed(services)
    before = await _rows(services)
    await usage_reprice.recompute_costs(services.db, new_price)
    after = await _rows(services)
    for run, row in after.items():
        assert row.tags == before[run].tags and row.estimated == before[run].estimated
        assert (row.prompt_tokens, row.cached_tokens) == (
            before[run].prompt_tokens,
            before[run].cached_tokens,
        )
    assert after["r6"].tags == "team:a"


async def test_second_run_changes_nothing(services: Services) -> None:
    """Wave 2: a completed run stores a high-water mark; the next run scans only newer rows."""
    await _seed(services)
    await usage_reprice.recompute_costs(services.db, new_price)
    first = {k: r.cost_usd for k, r in (await _rows(services)).items()}
    again = await usage_reprice.recompute_costs(services.db, new_price)
    assert (again.scanned, again.repriced, again.unchanged, again.unpriced) == (0, 0, 0, 0)
    assert {k: r.cost_usd for k, r in (await _rows(services)).items()} == first


async def test_all_rescans_every_row_and_finds_them_current(services: Services) -> None:
    await _seed(services)
    await usage_reprice.recompute_costs(services.db, new_price)
    first = {k: r.cost_usd for k, r in (await _rows(services)).items()}
    again = await usage_reprice.recompute_costs(services.db, new_price, all_rows=True)
    assert (again.scanned, again.repriced, again.unchanged, again.unpriced) == (4, 0, 3, 1)
    assert {k: r.cost_usd for k, r in (await _rows(services)).items()} == first
    assert again.old_total == pytest.approx(again.new_total) and again.all_rows is True


async def _mark(svc: Services) -> str | None:
    async with svc.db.session() as s:
        row = await s.get(Meta, usage_reprice.MARK_KEY)
        return None if row is None else row.value


async def test_the_mark_is_the_highest_scanned_id_and_newer_rows_are_scanned(services: Services) -> None:
    await _seed(services)
    report = await usage_reprice.recompute_costs(services.db, new_price)
    rows = await _rows(services)
    assert usage_reprice.MARK_KEY == "usage_reprice_mark"
    assert await _mark(services) == str(rows["r6"].id) == str(report.mark)
    await UsageLedger(services.db, old_price).record("s", "r7", "m", Usage(1000, 100, cached_tokens=800))
    later = await usage_reprice.recompute_costs(services.db, new_price)
    assert (later.scanned, later.repriced) == (1, 1)
    assert (await _rows(services))["r7"].cost_usd == pytest.approx(380e-6)
    assert await _mark(services) == str((await _rows(services))["r7"].id)


async def test_a_dry_run_leaves_the_mark(services: Services) -> None:
    await _seed(services)
    await usage_reprice.recompute_costs(services.db, new_price, dry_run=True)
    assert await _mark(services) is None
    await usage_reprice.recompute_costs(services.db, new_price)
    mark = await _mark(services)
    await UsageLedger(services.db, old_price).record("s", "r7", "m", Usage(1000, 100, cached_tokens=800))
    dry = await usage_reprice.recompute_costs(services.db, new_price, dry_run=True)
    assert dry.scanned == 1 and await _mark(services) == mark


async def test_a_run_that_scans_nothing_keeps_the_mark(services: Services) -> None:
    await _seed(services)
    await usage_reprice.recompute_costs(services.db, new_price)
    mark = await _mark(services)
    await usage_reprice.recompute_costs(services.db, new_price)
    assert await _mark(services) == mark


async def test_dry_run_reports_without_writing(services: Services) -> None:
    await _seed(services)
    before = {k: r.cost_usd for k, r in (await _rows(services)).items()}
    report = await usage_reprice.recompute_costs(services.db, new_price, dry_run=True)
    assert (report.repriced, report.dry_run) == (3, True)
    assert report.old_total == pytest.approx(3300e-6) and report.new_total == pytest.approx(2230e-6)
    assert {k: r.cost_usd for k, r in (await _rows(services)).items()} == before


async def test_days_limits_the_window(services: Services) -> None:
    await _seed(services)
    report = await usage_reprice.recompute_costs(services.db, new_price, days=30)
    after = await _rows(services)
    assert (report.scanned, report.repriced) == (3, 2)
    assert after["r6"].cost_usd == pytest.approx(1100e-6)  # 40 days old: outside the window


async def test_a_failing_or_unpriced_model_never_erases_a_cost(services: Services) -> None:
    await _seed(services)

    def flaky(model: str, p: int, c: int, /, **cache: int) -> float | None:
        if cache.get("cache_write_tokens"):
            raise RuntimeError("price table unavailable")
        return None

    report = await usage_reprice.recompute_costs(services.db, flaky)
    after = await _rows(services)
    assert (report.repriced, report.unpriced) == (0, 4)
    assert after["r1"].cost_usd == pytest.approx(1100e-6) and after["r2"].cost_usd == pytest.approx(1100e-6)


async def test_a_row_without_a_cost_gets_one_when_the_model_is_priced_now(services: Services) -> None:
    await UsageLedger(services.db, lambda *a, **k: None).record(
        "s", "r9", "m", Usage(1000, 100, cached_tokens=800)
    )
    report = await usage_reprice.recompute_costs(services.db, new_price)
    assert report.repriced == 1 and (await _rows(services))["r9"].cost_usd == pytest.approx(380e-6)


# ------------------------------------------------------------------ CLI
@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, workdir: Path) -> Path:
    monkeypatch.setenv("AUOREO_GATEWAY", "fake")
    monkeypatch.setenv("AUOREO_PORT", "9")
    monkeypatch.chdir(workdir)
    return workdir


def _seed_cli(home: Path) -> None:
    """One estimated row priced the old way, written through a real state.db."""
    from auoreo.cli.common import with_services

    async def go(svc: Services) -> None:
        await UsageLedger(svc.db, old_price).record("s", "r1", "m", Usage(1000, 100, cached_tokens=800))

    with_services(go, memory=False)


def _cost(home: Path) -> float:
    import sqlite3

    con = sqlite3.connect(home / "state.db")
    try:
        return float(con.execute("SELECT cost_usd FROM usage WHERE run_id = 'r1'").fetchone()[0])
    finally:
        con.close()


def test_cli_refuses_without_cached_pricing(cli_env: Path, home: Path) -> None:
    _seed_cli(home)
    r = runner.invoke(app, ["db", "recompute-costs"])
    assert r.exit_code == 2 and "m7.token.cached_pricing" in r.output
    assert _cost(home) == pytest.approx(1100e-6)


def test_cli_dry_run_then_real_run(cli_env: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUOREO_M7", '{"token": {"cached_pricing": true}}')
    _seed_cli(home)
    dry = runner.invoke(app, ["db", "recompute-costs", "--dry-run", "--json"])
    assert dry.exit_code == 0, dry.output
    report = json.loads(dry.stdout)
    assert report["dry_run"] is True and report["repriced"] == 1 and report["days"] is None
    assert _cost(home) == pytest.approx(1100e-6)
    real = runner.invoke(app, ["db", "recompute-costs", "--days", "7"])
    assert real.exit_code == 0 and "re-priced 1" in real.output
    assert _cost(home) == pytest.approx(380e-6)  # the fake gateway's cache-aware price
    again = runner.invoke(app, ["db", "recompute-costs", "--json"])
    assert json.loads(again.stdout)["scanned"] == 0  # above the mark only
    full = runner.invoke(app, ["db", "recompute-costs", "--all", "--json"])
    report = json.loads(full.stdout)
    assert (report["scanned"], report["repriced"], report["all_rows"]) == (1, 0, True)


def test_startup_never_reprices(cli_env: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUOREO_M7", '{"token": {"cached_pricing": true}}')
    _seed_cli(home)
    _seed_cli(home)  # a second full Services start and stop
    assert _cost(home) == pytest.approx(1100e-6)
