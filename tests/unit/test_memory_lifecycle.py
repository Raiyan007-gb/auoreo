"""Fact lifecycle (memory.db v1): migration, archive/expiry filtering, usage, supersede,
forget vs purge, trust feedback."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from auoreo.core.db import AuditRow
from auoreo.core.services import Services
from auoreo.memory import ledger
from auoreo.memory.migrations import VERSION, user_version
from auoreo.memory.store import MemoryStore

V0_SCHEMA = """
CREATE TABLE facts (
  id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT 'local', content TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'user', trust REAL NOT NULL DEFAULT 0.5,
  embed_model TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
  UNIQUE(tenant_id, content));
CREATE VIRTUAL TABLE facts_fts USING fts5(
  content, content='facts', content_rowid='id', tokenize='unicode61 remove_diacritics 1');
CREATE TRIGGER facts_ai AFTER INSERT ON facts BEGIN
  INSERT INTO facts_fts(rowid, content) VALUES (new.id, new.content); END;
CREATE TRIGGER facts_ad AFTER DELETE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, content) VALUES('delete', old.id, old.content); END;
CREATE TRIGGER facts_au AFTER UPDATE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, content) VALUES('delete', old.id, old.content);
  INSERT INTO facts_fts(rowid, content) VALUES (new.id, new.content); END;
"""


def _store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    monkeypatch.setattr(MemoryStore, "_load_vec", lambda self: False)
    return MemoryStore(tmp_path / "m.db", dims=8)


def test_v0_database_is_migrated_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "m.db"
    db = sqlite3.connect(path)
    db.executescript(V0_SCHEMA)
    db.execute(
        "INSERT INTO facts(content, category, trust, created_at, updated_at) VALUES (?,?,?,?,?)",
        ("legacy fact about postgres tuning", "user", 0.7, 1000.0, 1000.0),
    )
    db.commit()
    assert user_version(db) == 0
    db.close()

    s = _store(tmp_path, monkeypatch)
    cols = {r[1] for r in s.db.execute("PRAGMA table_info(facts)")}
    assert {
        "archived_at",
        "last_used_at",
        "use_count",
        "valid_from",
        "valid_to",
        "supersedes_id",
        "pinned",
        "source",
    } <= cols
    assert user_version(s.db) == VERSION
    row = s.get_fact(1)
    assert row is not None
    assert row["content"] == "legacy fact about postgres tuning" and row["trust"] == 0.7
    assert row["use_count"] == 0 and row["pinned"] == 0 and row["source"] == "agent"
    assert row["valid_from"] == 1000.0 and row["archived_at"] is None
    assert s.fts("fact", "postgres tuning", 5) == [1]  # FTS index survives
    s.close()
    again = _store(tmp_path, monkeypatch)  # idempotent re-open
    assert user_version(again.db) == VERSION and again.get_fact(1) is not None
    again.close()


def test_newer_schema_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = sqlite3.connect(tmp_path / "m.db")
    db.execute(f"PRAGMA user_version = {VERSION + 5}")
    db.close()
    with pytest.raises(RuntimeError, match="newer"):
        _store(tmp_path, monkeypatch)


def test_archived_and_expired_rows_are_not_retrieved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _store(tmp_path, monkeypatch)
    a, _ = s.add_fact("kafka cluster lives in frankfurt", "user", 0.5, None, None)
    b, _ = s.add_fact("kafka retention is seven days", "user", 0.5, None, None)
    c, _ = s.add_fact("kafka brokers run version three", "user", 0.5, None, None)
    assert set(s.fts("fact", "kafka", 10)) == {a, b, c}
    assert s.archive_fact(a) and not s.archive_fact(a)
    s.db.execute("UPDATE facts SET valid_to=? WHERE id=?", (time.time() - 1, b))
    s.db.execute("UPDATE facts SET valid_to=? WHERE id=?", (time.time() + 3600, c))  # future: still valid
    s.db.commit()
    assert s.fts("fact", "kafka", 10) == [c]
    assert set(s.fts("fact", "kafka", 10, include_archived=True)) == {a, b, c}
    assert [i for i, _ in s.find_facts("kafka")] == [c]
    # re-adding an archived text revives it
    fid, created = s.add_fact("kafka cluster lives in frankfurt", "user", 0.5, None, None)
    assert fid == a and not created and a in s.fts("fact", "frankfurt", 5)
    s.close()


async def test_service_search_filters_and_bumps_usage(services: Services) -> None:
    mem = services.memory
    assert mem is not None and mem.store.vec_enabled  # KNN arm must filter too
    keep, _ = await mem.add_fact("The staging database is called quartz")
    gone, _ = await mem.add_fact("The staging database used to be called onyx")
    await mem.forget(gone)
    hits = await mem.search("what is the staging database called")
    assert [h.id for h in hits if h.kind == "fact"] == [keep]
    row = await mem.get_fact(keep)
    assert row is not None and row["use_count"] == 1 and row["last_used_at"] is not None
    assert (await mem.get_fact(gone) or {})["use_count"] == 0
    archived = await mem.search("staging database onyx", include_archived=True)
    assert gone in [h.id for h in archived]


async def test_replace_supersedes_instead_of_rewriting(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    old, _ = await mem.add_fact("Deploys run on Fridays", source="user")
    new = await mem.replace_fact(old, "Deploys run on Mondays")
    assert new is not None and new != old
    new_row, old_row = await mem.get_fact(new), await mem.get_fact(old)
    assert new_row is not None and old_row is not None
    assert new_row["supersedes_id"] == old and new_row["source"] == "user"
    assert old_row["content"] == "Deploys run on Fridays" and old_row["valid_to"] is not None
    assert [h.id for h in await mem.search("when do deploys run")] == [new]
    assert old in [h.id for h in await mem.search("deploys fridays", include_archived=True)]
    newer = await mem.replace_fact(new, "Deploys run on Tuesdays")  # chain
    assert newer is not None and (await mem.get_fact(newer) or {})["supersedes_id"] == new
    assert await mem.replace_fact(old, "anything") is None  # superseded rows are not active


async def test_forget_archives_purge_deletes(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    fid, _ = await mem.add_fact("temporary note about a flaky test")
    assert await mem.forget(fid, actor="user", reason="obsolete")
    row = await mem.get_fact(fid)
    assert row is not None and row["archived_at"] is not None  # never deleted
    lines = ledger.read(services.settings.home)
    assert lines[-1]["action"] == "forget" and lines[-1]["fact_id"] == fid and lines[-1]["actor"] == "user"
    async with services.db.session() as sess:
        actions = [r.action for r in (await sess.execute(select(AuditRow))).scalars()]
    assert "memory.forget" in actions
    assert await mem.purge(fid)
    assert await mem.get_fact(fid) is None
    assert ledger.read(services.settings.home)[-1]["action"] == "purge"


async def test_feedback_clamps(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    fid, _ = await mem.add_fact("prefers tabs over spaces")
    assert await mem.feedback(fid, True) == pytest.approx(0.55)
    for _ in range(20):
        trust = await mem.feedback(fid, True)
    assert trust == 1.0
    for _ in range(20):
        trust = await mem.feedback(fid, False)
    assert trust == 0.0
    assert await mem.feedback(999_999, True) is None


async def test_pin_round_trip(services: Services) -> None:
    mem = services.memory
    assert mem is not None
    fid, _ = await mem.add_fact("the release manager is dana")
    assert await mem.pin(fid)
    assert (await mem.get_fact(fid) or {})["pinned"] == 1
    assert await mem.pin(fid, False)
    assert (await mem.get_fact(fid) or {})["pinned"] == 0
    assert not await mem.pin(424242)


async def test_close_waits_for_inflight_store_call_and_later_calls_fail_cleanly(services) -> None:  # type: ignore[no-untyped-def]
    import asyncio
    import threading
    import time

    import pytest

    mem = services.memory
    if mem is None:
        pytest.skip("memory disabled")
    started, order = threading.Event(), []

    def slow() -> str:
        started.set()
        time.sleep(0.3)
        order.append("call-done")
        return "ok"

    task = asyncio.create_task(mem._run(slow))
    await asyncio.to_thread(started.wait, 2)
    task.cancel()  # the worker thread keeps running, as with a cancelled turn
    await asyncio.to_thread(mem.close)
    order.append("closed")
    assert order == ["call-done", "closed"]
    with pytest.raises(RuntimeError, match="closed"):
        await mem._run(lambda: "late")
