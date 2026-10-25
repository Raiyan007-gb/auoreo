from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from auoreo.core.db import Base, Database, SchemaTooNewError, SessionRow, UsageRow

# The state.db DDL exactly as M2 (981b59b) created it with `create_all`.
M2_DDL = """
CREATE TABLE schema_meta ("key" VARCHAR(64) NOT NULL, value VARCHAR(255) NOT NULL, PRIMARY KEY ("key"));
CREATE TABLE sessions (
    id VARCHAR(64) NOT NULL, title VARCHAR(255) NOT NULL, agent VARCHAR(64) NOT NULL,
    workflow VARCHAR(64) NOT NULL, model VARCHAR(255), status VARCHAR(32) NOT NULL,
    parent_id VARCHAR(64), created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id));
CREATE TABLE events (
    seq INTEGER NOT NULL, aggregate_id VARCHAR(64) NOT NULL, aggregate_seq INTEGER NOT NULL,
    type VARCHAR(64) NOT NULL, data JSON NOT NULL, created_at DATETIME NOT NULL, PRIMARY KEY (seq));
CREATE UNIQUE INDEX ix_events_agg ON events (aggregate_id, aggregate_seq);
CREATE TABLE messages (
    id INTEGER NOT NULL, session_id VARCHAR(64) NOT NULL, idx INTEGER NOT NULL, message JSON NOT NULL,
    created_at DATETIME NOT NULL, PRIMARY KEY (id),
    FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE);
CREATE UNIQUE INDEX ix_messages_session ON messages (session_id, idx);
CREATE TABLE audit (
    seq INTEGER NOT NULL, ts DATETIME NOT NULL, session_id VARCHAR(64), actor VARCHAR(64) NOT NULL,
    action VARCHAR(64) NOT NULL, decision VARCHAR(32) NOT NULL, reason VARCHAR(64) NOT NULL,
    detail JSON NOT NULL, prev_hash VARCHAR(64) NOT NULL, row_hash VARCHAR(64) NOT NULL, PRIMARY KEY (seq));
CREATE TABLE idempotency (
    "key" VARCHAR(128) NOT NULL, result JSON NOT NULL, created_at DATETIME NOT NULL, PRIMARY KEY ("key"));
INSERT INTO schema_meta VALUES ('schema_version', '1');
INSERT INTO sessions VALUES ('ses_m2', 'old chat', 'default', 'chat', NULL, 'idle', NULL,
    '2026-09-01 10:00:00.000000', '2026-09-01 10:05:00.000000');
INSERT INTO messages VALUES (1, 'ses_m2', 0, '{"role": "user", "content": "hi from M2"}',
    '2026-09-01 10:00:00.000000');
"""


def make_m2(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(M2_DDL)
    con.commit()
    con.close()


def columns(path: Path, table: str) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


def scalar(path: Path, sql: str) -> object:
    con = sqlite3.connect(path)
    try:
        row = con.execute(sql).fetchone()
        return row[0] if row else None
    finally:
        con.close()


async def test_m2_database_is_migrated_in_place(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    make_m2(path)
    db = Database(path)
    await db.init()
    try:
        async with db.session() as s:
            row = await s.get(SessionRow, "ses_m2")
            assert row is not None and row.title == "old chat" and row.archived is False
            s.add(UsageRow(session_id="ses_m2", run_id="r", model="m", prompt_tokens=1))
            await s.commit()
    finally:
        await db.close()
    assert "archived" in columns(path, "sessions")
    assert {"usage", "messages_archive"} <= {
        r for (r,) in sqlite3.connect(path).execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert scalar(path, "SELECT value FROM schema_meta WHERE key='schema_version'") == "3"
    assert scalar(path, "SELECT version_num FROM alembic_version") == "0003"
    assert scalar(path, "SELECT message FROM messages WHERE session_id='ses_m2'") == (
        '{"role": "user", "content": "hi from M2"}'
    )
    db2 = Database(path)  # second start: nothing to do, still fine
    await db2.init()
    await db2.close()


async def test_fresh_database_matches_the_orm(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"
    db = Database(path)
    await db.init()
    await db.close()
    assert scalar(path, "SELECT version_num FROM alembic_version") == "0003"
    assert scalar(path, "SELECT value FROM schema_meta WHERE key='schema_version'") == "3"
    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []


async def test_newer_schema_is_refused_untouched(tmp_path: Path) -> None:
    path = tmp_path / "future.db"
    make_m2(path)
    con = sqlite3.connect(path)
    con.execute("UPDATE schema_meta SET value = '99' WHERE key = 'schema_version'")
    con.commit()
    con.close()
    db = Database(path)
    with pytest.raises(SchemaTooNewError):
        await db.init()
    await db.close()
    assert "archived" not in columns(path, "sessions")


async def test_memory_database_still_uses_create_all() -> None:
    db = Database(":memory:")
    await db.init()
    assert await db.scalar_count(UsageRow) == 0
    await db.close()
