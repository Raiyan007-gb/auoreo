"""memory.db v2 (spec §25.8): fact_entities + communities + chunk provenance, upgraded in place."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from auoreo.memory import graph, migrations
from auoreo.memory.store import SCHEMA, MemoryStore


def _v1_db(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    migrations._v1(db)
    db.execute("PRAGMA user_version = 1")
    now = time.time()
    db.execute(
        "INSERT INTO facts(content, created_at, updated_at, valid_from) VALUES (?,?,?,?)",
        ("Priya Raman leads the Payments Platform team.", now, now, now),
    )
    db.execute(
        "INSERT INTO chunks(doc_id, source, start_line, end_line, text, tokens, sha, created_at) "
        "VALUES ('d1', 'notes.md', 1, 2, 'old chunk text', 3, 'abc', ?)",
        (now,),
    )
    db.commit()
    db.close()


def _tables(db: sqlite3.Connection) -> set[str]:
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")}


def test_v1_database_upgrades_in_place(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    _v1_db(path)
    store = MemoryStore(path, dims=8)
    try:
        assert migrations.VERSION >= 2 and migrations.user_version(store.db) == migrations.VERSION
        assert {"fact_entities", "fact_entities_entity", "communities"} <= _tables(store.db)
        cols = migrations._columns(store.db, "chunks")
        assert {"chunk_strategy", "span_start", "span_end", "doc_id"} <= cols
        row = store.db.execute("SELECT chunk_strategy, span_start, span_end, doc_id FROM chunks").fetchone()
        assert row == ("early", None, None, "d1")  # existing rows keep working, default strategy
        assert store.fetch("chunk", [1])[1].extra["strategy"] == "early"
        # facts written before v2 have no entity rows until the graph build backfills them
        assert store.db.execute("SELECT count(*) FROM fact_entities").fetchone()[0] == 0
        assert graph.backfill(store) == 1
        assert graph.entities_of(store, 1) == ["payments platform", "priya raman"]
    finally:
        store.close()


def test_v2_step_is_idempotent_and_reopen_is_a_noop(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    MemoryStore(path, dims=8).close()
    db = sqlite3.connect(path)
    migrations._v2(db)  # a crash between DDL and the version bump re-runs the step
    db.commit()
    assert migrations.migrate(db) == migrations.VERSION
    db.close()


def test_newer_schema_refuses(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    MemoryStore(path, dims=8).close()
    db = sqlite3.connect(path)
    db.execute("PRAGMA user_version = 99")
    db.commit()
    db.close()
    with pytest.raises(RuntimeError, match="newer"):
        MemoryStore(path, dims=8)


def test_entity_rows_follow_fact_lifecycle(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db", dims=8)
    try:
        a, _ = store.add_fact("Dana reports to Hiroshi Tanaka.", "user", 0.5, None, None)
        b, _ = store.add_fact("Kestrel Labs builds in Elixir.", "user", 0.5, None, None)
        graph.index_fact(store, a, "Dana reports to Hiroshi Tanaka.")
        graph.index_fact(store, b, "Kestrel Labs builds in Elixir.")
        assert graph.entities_of(store, a) == ["dana", "hiroshi tanaka"]
        store.archive_fact(a)
        assert graph.entities_of(store, a) == []
        store.purge_fact(b)
        assert graph.entities_of(store, b) == []
        c, _ = store.add_fact("Sam works at Bluefin Studio.", "user", 0.5, None, None)
        graph.index_fact(store, c, "Sam works at Bluefin Studio.")
        new = store.replace_fact(c, "Sam works at Bluefin Studio in Porto.", None)
        assert new is not None and graph.entities_of(store, c) == []  # superseded = expired
        # a future expiry keeps the rows (temporal validity is not an archive)
        store.db.execute("UPDATE facts SET valid_to=? WHERE id=?", (time.time() + 3600, new))
        graph.index_fact(store, new, "Sam works at Bluefin Studio in Porto.")
        store.db.execute("UPDATE facts SET valid_to=? WHERE id=?", (time.time() + 7200, new))
        assert "bluefin studio" in graph.entities_of(store, new)
    finally:
        store.close()
