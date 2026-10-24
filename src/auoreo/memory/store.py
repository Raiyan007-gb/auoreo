"""Synchronous SQLite store for memory: FTS5 + sqlite-vec hybrid (spec §11.1).

Lives in `<home>/memory.db`. All methods are blocking; `MemoryService` calls them through
`asyncio.to_thread` under a single-writer lock. If the `vec0` extension cannot load, the
store runs FTS-only (a tested path, never an error).
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "i",
        "in",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "this",
        "to",
        "was",
        "we",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
        "how",
        "do",
        "does",
        "did",
    ]
)

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


def fts_query(raw: str) -> str | None:
    """Sanitize free text into a safe FTS5 query: quoted terms OR-joined.

    Returns None when nothing survives — callers then return zero results instead of
    sending an invalid MATCH expression (never an SQL error)."""
    terms = [t for t in re.findall(r"\w+", raw.lower()) if len(t) >= 2 and t not in STOPWORDS]
    if not terms:
        return None
    seen: list[str] = []
    for t in terms:
        if t not in seen:
            seen.append(t)
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in seen[:32])


def sha(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()


@dataclass
class Row:
    kind: str  # fact | chunk | transcript
    id: int
    text: str
    source: str | None
    trust: float
    extra: dict[str, object]


class MemoryStore:
    def __init__(self, path: Path | str, dims: int, tenant: str = "local") -> None:
        self.path = str(path)
        self.dims = dims
        self.tenant = tenant
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
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
    ) -> tuple[int, bool]:
        now = time.time()
        cur = self.db.execute(
            "INSERT INTO facts(tenant_id, content, category, trust, embed_model, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(tenant_id, content) DO NOTHING RETURNING id",
            (self.tenant, content, category, trust, embed_model if embedding else None, now, now),
        )
        row = cur.fetchone()
        if row is None:
            existing = self.db.execute(
                "SELECT id FROM facts WHERE tenant_id=? AND content=?", (self.tenant, content)
            ).fetchone()
            self.db.commit()
            return int(existing[0]), False
        fid = int(row[0])
        if embedding is not None:
            self._put_vec("facts_vec", fid, embedding)
        self.db.commit()
        return fid, True

    def replace_fact(self, fact_id: int, content: str, embedding: list[float] | None) -> bool:
        cur = self.db.execute(
            "UPDATE facts SET content=?, updated_at=? WHERE id=? AND tenant_id=?",
            (content, time.time(), fact_id, self.tenant),
        )
        if cur.rowcount and embedding is not None:
            self._put_vec("facts_vec", fact_id, embedding)
        self.db.commit()
        return cur.rowcount > 0

    def remove_fact(self, fact_id: int) -> bool:
        cur = self.db.execute("DELETE FROM facts WHERE id=? AND tenant_id=?", (fact_id, self.tenant))
        if cur.rowcount and self.vec_enabled:
            self.db.execute(f"DELETE FROM facts_vec_{self.dims} WHERE rowid=?", (fact_id,))
        self.db.commit()
        return cur.rowcount > 0

    def find_facts(self, substring: str) -> list[tuple[int, str]]:
        rows = self.db.execute(
            "SELECT id, content FROM facts WHERE tenant_id=? AND instr(content, ?) > 0",
            (self.tenant, substring),
        ).fetchall()
        return [(int(r[0]), str(r[1])) for r in rows]

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
    ) -> bool:
        cur = self.db.execute(
            "INSERT INTO chunks(tenant_id, doc_id, source, start_line, end_line, text, tokens, sha, "
            "embed_model, created_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(tenant_id, sha) DO NOTHING RETURNING id",
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
            (session_id, role, text, time.time()),
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
    def fts(self, kind: str, query: str, limit: int) -> list[int]:
        q = fts_query(query)
        if q is None:
            return []
        table, base = {"fact": ("facts_fts", "facts"), "chunk": ("chunks_fts", "chunks")}[kind]
        rows = self.db.execute(
            f"SELECT f.rowid FROM {table} f JOIN {base} b ON b.id = f.rowid "
            f"WHERE {table} MATCH ? AND b.tenant_id = ? ORDER BY rank LIMIT ?",
            (q, self.tenant, limit),
        ).fetchall()
        return [int(r[0]) for r in rows]

    def knn(self, kind: str, embedding: list[float], limit: int) -> list[int]:
        if not self.vec_enabled or len(embedding) != self.dims:
            return []
        import sqlite_vec

        table = {"fact": "facts_vec", "chunk": "chunks_vec"}[kind]
        rows = self.db.execute(
            f"SELECT rowid FROM {table}_{self.dims} WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (sqlite_vec.serialize_float32(embedding), limit),
        ).fetchall()
        return [int(r[0]) for r in rows]

    def fetch(self, kind: str, ids: list[int]) -> dict[int, Row]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        if kind == "fact":
            rows = self.db.execute(
                f"SELECT id, content, category, trust FROM facts WHERE tenant_id=? AND id IN ({marks})",
                (self.tenant, *ids),
            ).fetchall()
            return {int(r[0]): Row("fact", int(r[0]), r[1], r[2], float(r[3]), {}) for r in rows}
        rows = self.db.execute(
            "SELECT id, text, source, start_line, end_line FROM chunks "
            f"WHERE tenant_id=? AND id IN ({marks})",
            (self.tenant, *ids),
        ).fetchall()
        return {
            int(r[0]): Row("chunk", int(r[0]), r[1], r[2], -1.0, {"start": r[3], "end": r[4]}) for r in rows
        }

    def search_transcript(self, query: str, limit: int, session_id: str | None) -> list[Row]:
        q = fts_query(query)
        if q is None:
            return []
        sql = (
            "SELECT t.id, t.text, t.session_id, t.role FROM transcript_fts f JOIN transcript t "
            "ON t.id = f.rowid WHERE transcript_fts MATCH ?"
        )
        args: list[object] = [q]
        if session_id:
            sql += " AND t.session_id = ?"
            args.append(session_id)
        sql += " ORDER BY rank LIMIT ?"
        args.append(limit)
        return [
            Row("transcript", int(r[0]), r[1], r[2], -1.0, {"role": r[3]})
            for r in self.db.execute(sql, args).fetchall()
        ]

    def bump_trust(self, fact_id: int, delta: float) -> None:
        self.db.execute("UPDATE facts SET trust = MIN(1.0, MAX(0.0, trust + ?)) WHERE id=?", (delta, fact_id))
        self.db.commit()

    def counts(self) -> dict[str, int]:
        return {
            t: int(self.db.execute(f"SELECT count(*) FROM {t}").fetchone()[0])
            for t in ("facts", "chunks", "transcript")
        }
