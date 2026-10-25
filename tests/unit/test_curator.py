"""Deterministic curator: thresholds, pinning, duplicate merge, ledger, guards, report."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from auoreo.core.services import Services
from auoreo.memory import curator as curator_mod
from auoreo.memory import ledger
from auoreo.memory.curator import Curator, overlap

DAY = 86_400


def backdate(svc: Services, fact_id: int, days: float) -> None:
    assert svc.memory is not None
    svc.memory.store.db.execute(
        "UPDATE facts SET created_at = created_at - ?, updated_at = updated_at - ? WHERE id=?",
        (days * DAY, days * DAY, fact_id),
    )
    svc.memory.store.db.commit()


async def add(svc: Services, text: str, days: float = 0) -> int:
    assert svc.memory is not None
    fid, _ = await svc.memory.add_fact(text)
    if days:
        backdate(svc, fid, days)
    return fid


def test_overlap() -> None:
    assert overlap("Deploy with make ship", "deploy with make ship!") == 1.0
    assert overlap("a b c d e f g h i j", "a b c d e f g h i x") == 0.9
    assert overlap("a b c d e f g h i j", "a b c d e f g h x y") < 0.9


async def test_starter_exposes_curator(services: Services) -> None:
    cur = services.ext["curator"]
    assert isinstance(cur, Curator) and cur._task is not None  # loop scheduled, first check later
    assert curator_mod.get(services) is cur


async def test_thresholds_pinned_and_ledger(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    fresh = await add(services, "fresh fact about grafana dashboards")
    stale = await add(services, "stale fact about jenkins pipelines", days=45)
    old = await add(services, "old rumour about the mainframe", days=100)
    pinned = await add(services, "pinned fact about the vpn gateway", days=200)
    used = await add(services, "used fact about terraform modules", days=200)
    await mem.pin(pinned)
    mem.store.bump_use([used])  # a recall (search() bumps every returned fact)
    rep = await Curator(services).run(force=True)
    assert rep.skipped is None and rep.scanned == 5
    assert rep.archived == [old] and rep.stale == [stale] and rep.skipped_pinned == 1
    rows = {i: await mem.get_fact(i) for i in (fresh, stale, old, pinned, used)}
    assert rows[old] and rows[old]["archived_at"] is not None
    assert all(rows[i] and rows[i]["archived_at"] is None for i in (fresh, stale, pinned, used))
    lines = [e for e in ledger.read(services.settings.home) if e["actor"] == "curator"]
    assert lines and lines[-1]["action"] == "archive" and lines[-1]["fact_id"] == old
    assert "unused for 100 days" in lines[-1]["reason"]
    report = curator_mod.latest_report(services.settings.home)
    assert report is not None and report.parent.parent == services.settings.home / "logs" / "curator"
    text = report.read_text()
    assert "old rumour about the mainframe" in text and "## Stale" in text
    assert rep.report_path == str(report)


async def test_duplicates_merge_into_newest(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    a = await add(services, "The user deploys with make ship on Fridays", days=10)
    b = await add(services, "the user deploys with make ship on fridays!", days=5)
    c = await add(services, "The user deploys with `make ship` on Fridays.")
    p = await add(services, "Pinned: the user deploys with make ship on Fridays", days=20)
    await mem.pin(p)
    other = await add(services, "The user prefers dark mode in every editor")
    rep = await Curator(services).run(force=True)
    merged = {(m.fact_id, m.into) for m in rep.duplicates_merged}
    assert merged == {(a, c), (b, c)}
    assert (await mem.get_fact(a) or {})["archived_at"] is not None
    assert (await mem.get_fact(p) or {})["archived_at"] is None  # pinned never touched
    assert (await mem.get_fact(other) or {})["archived_at"] is None
    lines = [e for e in ledger.read(services.settings.home) if e["action"] == "merge"]
    assert {(e["fact_id"], e["into"]) for e in lines} == {(a, c), (b, c)}


async def test_dry_run_touches_nothing(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    old = await add(services, "an ancient fact about floppy disks", days=120)
    cur = Curator(services)
    rep = await cur.run(dry_run=True)
    assert rep.dry_run and rep.archived == [old]
    assert (await mem.get_fact(old) or {})["archived_at"] is None
    assert not cur.state_path.exists()  # dry runs never update the interval state
    assert not ledger.ledger_path(services.settings.home).exists()
    assert rep.report_path and "(dry run)" in read(rep.report_path)


async def test_guards_idle_and_interval(services: Services) -> None:
    cfg = services.settings.memory
    cur = Curator(services)
    await services.create_session()  # activity just now
    rep = await cur.run()
    assert rep.skipped and "session activity" in rep.skipped
    cfg.curator_min_idle_hours = 0
    rep = await cur.run()
    assert rep.skipped is None
    state = json.loads(cur.state_path.read_text())
    assert state["last_run"] == pytest.approx(time.time(), abs=60)
    rep = await cur.run()
    assert rep.skipped and "last pass" in rep.skipped
    assert (await cur.run(force=True)).skipped is None  # --force ignores guards
    cur.state_path.write_text(json.dumps({"last_run": time.time() - 8 * DAY}))
    assert (await cur.run()).skipped is None  # interval elapsed


async def test_disabled_curator_has_no_loop(services: Services) -> None:
    services.settings.memory.curator_enabled = False
    cur = await curator_mod.start(services)
    assert cur._task is None
    await cur.close()


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")
