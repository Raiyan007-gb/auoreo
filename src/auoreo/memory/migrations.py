"""`PRAGMA user_version` migrations for `memory.db` (plain sqlite3, not Alembic).

`MemoryStore.__init__` creates the v0 schema (`IF NOT EXISTS`) and then runs every step
whose number is above the stored `user_version`, one transaction per step. Steps are
idempotent, so a crash between the DDL and the version bump simply re-runs the step.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

# v1 — fact lifecycle (spec §11.10, §24.3, §25.8): archive, usage, temporal validity,
# supersede chain, pinning and provenance.
_V1_COLUMNS: tuple[tuple[str, str], ...] = (
    ("archived_at", "REAL"),
    ("last_used_at", "REAL"),
    ("use_count", "INTEGER NOT NULL DEFAULT 0"),
    ("valid_from", "REAL"),
    ("valid_to", "REAL"),
    ("supersedes_id", "INTEGER"),
    ("pinned", "INTEGER NOT NULL DEFAULT 0"),
    ("source", "TEXT NOT NULL DEFAULT 'agent'"),
)


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(r[1]) for r in db.execute(f"PRAGMA table_info({table})").fetchall()}


def _v1(db: sqlite3.Connection) -> None:
    have = _columns(db, "facts")
    for name, decl in _V1_COLUMNS:
        if name not in have:
            db.execute(f"ALTER TABLE facts ADD COLUMN {name} {decl}")
    db.execute("UPDATE facts SET valid_from = created_at WHERE valid_from IS NULL")
    # Re-index FTS only when the text changes: recall bumps `use_count` on every hit.
    db.execute("DROP TRIGGER IF EXISTS facts_au")
    db.execute(
        "CREATE TRIGGER facts_au AFTER UPDATE OF content ON facts BEGIN "
        "INSERT INTO facts_fts(facts_fts, rowid, content) VALUES('delete', old.id, old.content); "
        "INSERT INTO facts_fts(rowid, content) VALUES (new.id, new.content); END"
    )
    db.execute("CREATE INDEX IF NOT EXISTS facts_active ON facts(tenant_id, archived_at, valid_to)")


# v2 — GraphRAG-lite + chunk provenance (spec §25.8). `chunks.doc_id` already exists in the
# v0 schema; it is only added when missing (hand-made or very old databases).
_V2_CHUNK_COLUMNS: tuple[tuple[str, str], ...] = (
    ("chunk_strategy", "TEXT DEFAULT 'early'"),
    ("span_start", "INTEGER"),
    ("span_end", "INTEGER"),
    ("doc_id", "TEXT"),
)
_NOW = "((julianday('now') - 2440587.5) * 86400.0)"


def _v2(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS fact_entities (fact_id INTEGER NOT NULL, entity TEXT NOT NULL, "
        "PRIMARY KEY(fact_id, entity))"
    )
    db.execute("CREATE INDEX IF NOT EXISTS fact_entities_entity ON fact_entities(entity)")
    db.execute(
        "CREATE TABLE IF NOT EXISTS communities (id INTEGER PRIMARY KEY, "
        "tenant_id TEXT NOT NULL DEFAULT 'local', level INTEGER NOT NULL, entities TEXT NOT NULL, "
        "fact_ids TEXT NOT NULL, summary TEXT NOT NULL, mtime REAL NOT NULL)"
    )
    have = _columns(db, "chunks")
    for name, decl in _V2_CHUNK_COLUMNS:
        if name not in have:
            db.execute(f"ALTER TABLE chunks ADD COLUMN {name} {decl}")
    # Entity rows follow the fact lifecycle: dropped on purge, archive or expiry.
    db.execute(
        "CREATE TRIGGER IF NOT EXISTS facts_entities_ad AFTER DELETE ON facts BEGIN "
        "DELETE FROM fact_entities WHERE fact_id = old.id; END"
    )
    db.execute(
        "CREATE TRIGGER IF NOT EXISTS facts_entities_au AFTER UPDATE OF archived_at, valid_to ON facts "
        f"WHEN new.archived_at IS NOT NULL OR (new.valid_to IS NOT NULL AND new.valid_to <= {_NOW} + 1.0) "
        "BEGIN DELETE FROM fact_entities WHERE fact_id = new.id; END"
    )


STEPS: tuple[Callable[[sqlite3.Connection], None], ...] = (_v1, _v2)
VERSION = len(STEPS)


def user_version(db: sqlite3.Connection) -> int:
    return int(db.execute("PRAGMA user_version").fetchone()[0])


def migrate(db: sqlite3.Connection) -> int:
    """Bring `db` to `VERSION`; returns the version it started at."""
    db.commit()
    start = user_version(db)
    if start > VERSION:
        raise RuntimeError(f"memory.db schema v{start} is newer than this code (v{VERSION})")
    for number, step in enumerate(STEPS, start=1):
        if number <= start:
            continue
        db.execute("BEGIN IMMEDIATE")
        try:
            step(db)
            db.execute(f"PRAGMA user_version = {number}")
            db.commit()
        except Exception:
            db.rollback()
            raise
    return start
