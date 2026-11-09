"""Synchronous SQLite store for memory: FTS5 + sqlite-vec hybrid (spec §11.1).

Lives in `<home>/memory.db`. All methods are blocking; `MemoryService` calls them through
`asyncio.to_thread` under a single-writer lock. If the `vec0` extension cannot load, the
store runs FTS-only (a tested path, never an error). Schema upgrades run through
`migrations.migrate` (`PRAGMA user_version`).

Fact lifecycle (v1): retrieval only sees *active* facts — not archived and not expired
(`valid_to` in the past, which is how superseded rows end). Nothing is deleted except by
`purge_fact`, which only the CLI calls.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Any

from auoreo.core.sqlcipher import open_memory
from auoreo.memory.migrations import migrate
from auoreo.memory.store_i4 import I4StoreMixin
from auoreo.memory.store_search import STOPWORDS, Row, SearchMixin, active, fts_query

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT 'local', content TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'user', trust REAL NOT NULL DEFAULT 0.5,
  embed_model TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
  UNIQUE(tenant_id, content));
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
  content, content='facts', content_rowid='id', tokenize='unicode61 remove_diacritics 1');
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
  INSERT INTO facts_fts(rowid, content) VALUES (new.id, new.content); END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, content) VALUES('delete', old.id, old.content); END;
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, content) VALUES('delete', old.id, old.content);
  INSERT INTO facts_fts(rowid, content) VALUES (new.id, new.content); END;

CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT 'local', doc_id TEXT NOT NULL,
  source TEXT NOT NULL, start_line INTEGER, end_line INTEGER, text TEXT NOT NULL,
  tokens INTEGER NOT NULL, sha TEXT NOT NULL, embed_model TEXT, created_at REAL NOT NULL,
  UNIQUE(tenant_id, sha));
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text, content='chunks', content_rowid='id', tokenize='unicode61 remove_diacritics 1');
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text); END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.id, old.text); END;

CREATE TABLE IF NOT EXISTS transcript (
  id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL,
  created_at REAL NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts USING fts5(
  text, content='transcript', content_rowid='id', tokenize='unicode61 remove_diacritics 1');
CREATE TRIGGER IF NOT EXISTS transcript_ai AFTER INSERT ON transcript BEGIN
  INSERT INTO transcript_fts(rowid, text) VALUES (new.id, new.text); END;
"""

FACT_COLUMNS = (
    "id, content, category, trust, created_at, updated_at, archived_at, last_used_at, use_count, "
    "valid_from, valid_to, supersedes_id, pinned, source, scope, owner_session, user_id, expires_at, "
    "half_life_days"
)
SOURCES = frozenset({"user", "agent", "curator", "ingest"})


def sha(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()


class MemoryStore(SearchMixin, I4StoreMixin):
    def __init__(
        self, path: Path | str, dims: int, tenant: str = "local", *, encryption: str | None = None
    ) -> None:
        self.path = str(path)
        self.dims = dims
        self.tenant = tenant
        # F6: sqlcipher3 + `PRAGMA key` when db_encryption=sqlcipher (else stdlib sqlite3)
        self.db, self.encrypted = open_memory(self.path, encryption)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        migrate(self.db)
        self.trigram = (
            self.db.execute("SELECT 1 FROM sqlite_master WHERE name='facts_tri'").fetchone() is not None
        )
        self.vec_enabled = self._load_vec()
        self.db.commit()

    def _load_vec(self) -> bool:
        try:
            import sqlite_vec

            self.db.enable_load_extension(True)
            sqlite_vec.load(self.db)
            self.db.enable_load_extension(False)
            self.db.execute("SELECT vec_version()").fetchone()
            for table in ("facts_vec", "chunks_vec"):
                self.db.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {table}_{self.dims} USING "
                    f"vec0(embedding float[{self.dims}] distance_metric=cosine)"
                )
            return True
        except Exception as exc:  # FTS-only fallback (spec §11.1)
            log.warning("sqlite-vec unavailable, memory runs FTS-only: %s", exc)
            return False

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------- writes
    def add_fact(
        self,
        content: str,
        category: str,
        trust: float,
        embedding: list[float] | None,
        embed_model: str | None,
        source: str = "agent",
        **meta: Any,
    ) -> tuple[int, bool]:
        """Insert a fact; re-adding a known text revives it when archived or expired.
        `meta`: scope, owner_session, user_id, expires_at, half_life_days (memory.db v3);
        `expires_at` also sets `valid_to`, so an expired fact ends like a superseded one."""
        now = time.time()
        expires = meta.get("expires_at")
        cur = self.db.execute(
            "INSERT INTO facts(tenant_id, content, category, trust, embed_model, created_at, "
            "updated_at, valid_from, source, scope, owner_session, user_id, expires_at, half_life_days, "
            "valid_to) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(tenant_id, content) DO NOTHING RETURNING id",
            (
                self.tenant,
                content,
                category,
                trust,
                embed_model if embedding else None,
                now,
                now,
                now,
                source if source in SOURCES else "agent",
                meta.get("scope") or "user",
                meta.get("owner_session"),
                meta.get("user_id"),
                expires,
                meta.get("half_life_days"),
                expires,
            ),
        )
        row = cur.fetchone()
        if row is None:
            existing = self.db.execute(
                "SELECT id FROM facts WHERE tenant_id=? AND content=?", (self.tenant, content)
            ).fetchone()
            self.db.execute(
                "UPDATE facts SET archived_at=NULL, valid_to=NULL, updated_at=? "
                f"WHERE id=? AND NOT ({active()})",
                (now, existing[0], now),
            )
            self.db.commit()
            return int(existing[0]), False
        fid = int(row[0])
        if embedding is not None:
            self._put_vec("facts_vec", fid, embedding)
        self.db.commit()
        return fid, True

    def replace_fact(self, fact_id: int, content: str, embedding: list[float] | None) -> int | None:
        """Supersede an active fact: the new text becomes a new row (`supersedes_id` = old id)
        and the old row gets `valid_to = now`, so it stays readable through the archive only.
        Returns the id carrying `content`, or None when `fact_id` is not active."""
        now = time.time()
        old = self.db.execute(
            f"SELECT content, category, trust, source FROM facts WHERE id=? AND tenant_id=? AND {active()}",
            (fact_id, self.tenant, now),
        ).fetchone()
        if old is None:
            return None
        if old[0] == content:
            return fact_id
        cur = self.db.execute(
            "INSERT INTO facts(tenant_id, content, category, trust, created_at, updated_at, valid_from, "
            "supersedes_id, source) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(tenant_id, content) DO UPDATE SET archived_at=NULL, valid_to=NULL, "
            "supersedes_id=excluded.supersedes_id, updated_at=excluded.updated_at RETURNING id",
            (self.tenant, content, old[1], old[2], now, now, now, fact_id, old[3]),
        )
        new_id = int(cur.fetchone()[0])
        self.db.execute("UPDATE facts SET valid_to=?, updated_at=? WHERE id=?", (now, now, fact_id))
        if embedding is not None:
            self._put_vec("facts_vec", new_id, embedding)
        self.db.commit()
        return new_id

    def purge_fact(self, fact_id: int) -> bool:
        """Hard delete. Only `auoreo memory purge --yes` reaches this; tools archive."""
        cur = self.db.execute("DELETE FROM facts WHERE id=? AND tenant_id=?", (fact_id, self.tenant))
        if cur.rowcount and self.vec_enabled:
            self.db.execute(f"DELETE FROM facts_vec_{self.dims} WHERE rowid=?", (fact_id,))
        self.db.commit()
        return cur.rowcount > 0

    def archive_fact(self, fact_id: int) -> bool:
        cur = self.db.execute(
            "UPDATE facts SET archived_at=? WHERE id=? AND tenant_id=? AND archived_at IS NULL",
            (time.time(), fact_id, self.tenant),
        )
        self.db.commit()
        return cur.rowcount > 0

    def set_pinned(self, fact_id: int, pinned: bool) -> bool:
        cur = self.db.execute(
            "UPDATE facts SET pinned=? WHERE id=? AND tenant_id=?", (int(pinned), fact_id, self.tenant)
        )
        self.db.commit()
        return cur.rowcount > 0

    def bump_use(self, ids: list[int]) -> None:
        """Record a successful recall (one batched UPDATE)."""
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        self.db.execute(
            f"UPDATE facts SET use_count = use_count + 1, last_used_at = ? WHERE id IN ({marks})",
            (time.time(), *ids),
        )
        self.db.commit()

    def absorb(self, keep: int, gone: int) -> None:
        """Duplicate merge: fold `gone`'s usage into `keep` (unless pinned), archive `gone`."""
        self.db.execute(
            "UPDATE facts SET use_count = use_count + (SELECT use_count FROM facts WHERE id=?), "
            "last_used_at = nullif(max(coalesce(last_used_at, 0), "
            "coalesce((SELECT last_used_at FROM facts WHERE id=?), 0)), 0) WHERE id=? AND pinned=0",
            (gone, gone, keep),
        )
        self.db.execute("UPDATE facts SET archived_at=? WHERE id=?", (time.time(), gone))
        self.db.commit()

    def add_chunk(
        self,
        doc_id: str,
        source: str,
        start: int,
        end: int,
        text: str,
        tokens: int,
        embedding: list[float] | None,
        embed_model: str | None,
        *,
        strategy: str = "early",
        span: tuple[int, int] | None = None,
    ) -> bool:
        lo, hi = span if span and span[0] >= 0 else (None, None)
        cur = self.db.execute(
            "INSERT INTO chunks(tenant_id, doc_id, source, start_line, end_line, text, tokens, sha, "
            "embed_model, created_at, chunk_strategy, span_start, span_end) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(tenant_id, sha) DO NOTHING RETURNING id",
            (
                self.tenant,
                doc_id,
                source,
                start,
                end,
                text,
                tokens,
                sha(text),
                embed_model if embedding else None,
                time.time(),
                strategy,
                lo,
                hi,
            ),
        )
        row = cur.fetchone()
        if row is not None and embedding is not None:
            self._put_vec("chunks_vec", int(row[0]), embedding)
        self.db.commit()
        return row is not None

    def add_transcript(self, session_id: str, role: str, text: str) -> None:
        self.db.execute(
            "INSERT INTO transcript(session_id, role, text, created_at) VALUES (?,?,?,?)",
            (session_id, self.transcript_tag() + role, text, time.time()),
        )
        self.db.commit()

    def _put_vec(self, table: str, rowid: int, embedding: list[float]) -> None:
        if not self.vec_enabled or len(embedding) != self.dims:
            return
        import sqlite_vec

        name = f"{table}_{self.dims}"
        self.db.execute(f"DELETE FROM {name} WHERE rowid=?", (rowid,))
        self.db.execute(
            f"INSERT INTO {name}(rowid, embedding) VALUES (?, ?)",
            (rowid, sqlite_vec.serialize_float32(embedding)),
        )

    # -------------------------------------------------------------- reads
    def find_facts(self, substring: str, *, include_archived: bool = False) -> list[tuple[int, str]]:
        where = "" if include_archived else f" AND {active()}"
        args: tuple[Any, ...] = (self.tenant, substring) + (() if include_archived else (time.time(),))
        rows = self.db.execute(
            f"SELECT id, content FROM facts WHERE tenant_id=? AND instr(content, ?) > 0{where}", args
        ).fetchall()
        return [(int(r[0]), str(r[1])) for r in rows]

    def get_fact(self, fact_id: int) -> dict[str, Any] | None:
        cur = self.db.execute(
            f"SELECT {FACT_COLUMNS} FROM facts WHERE id=? AND tenant_id=?", (fact_id, self.tenant)
        )
        row = cur.fetchone()
        return dict(zip([d[0] for d in cur.description], row, strict=True)) if row else None

    def fact_rows(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        where = f" AND {active()}" if active_only else ""
        args: tuple[Any, ...] = (self.tenant, time.time()) if active_only else (self.tenant,)
        cur = self.db.execute(f"SELECT {FACT_COLUMNS} FROM facts WHERE tenant_id=?{where} ORDER BY id", args)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]

    def bump_trust(self, fact_id: int, delta: float) -> float | None:
        self.db.execute(
            "UPDATE facts SET trust = MIN(1.0, MAX(0.0, trust + ?)) WHERE id=? AND tenant_id=?",
            (delta, fact_id, self.tenant),
        )
        self.db.commit()
        row = self.db.execute(
            "SELECT trust FROM facts WHERE id=? AND tenant_id=?", (fact_id, self.tenant)
        ).fetchone()
        return float(row[0]) if row else None

    def counts(self) -> dict[str, int]:
        out = {
            t: int(self.db.execute(f"SELECT count(*) FROM {t}").fetchone()[0])
            for t in ("facts", "chunks", "transcript")
        }
        out["archived"] = int(
            self.db.execute("SELECT count(*) FROM facts WHERE archived_at IS NOT NULL").fetchone()[0]
        )
        return out


__all__ = [
    "FACT_COLUMNS",
    "SCHEMA",
    "SOURCES",
    "STOPWORDS",
    "MemoryStore",
    "Row",
    "active",
    "fts_query",
    "sha",
]
