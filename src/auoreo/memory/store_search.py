"""Read side of `MemoryStore` (spec §11.1, §11.6): FTS5 (+ CJK trigram), vec KNN, fetch.

- FTS uses the `unicode61` tables; a query containing CJK text also probes the trigram
  mirrors (`*_tri`, memory.db v3) — trigram MATCH for runs ≥ 3 characters, a LIKE scan for
  shorter runs — because `unicode61` treats a whole CJK run as one token.
- KNN filters by the current `embed_model` (rows embedded by another model are invisible
  to the vector arm until the re-embed job rewrites them) and drops inactive facts.
- `fetch` returns scope/owner/decay columns in `Row.extra` for the service's predicates.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿]+")
STOPWORDS = frozenset(
    """
    a an and are as at be by for from has have i in is it its me my of on or our that the
    this to was we what when where which who why will with you your how do does did
    """.split()  # noqa: SIM905 — compact word list
)
_TABLES: dict[str, tuple[str, str, str, str]] = {
    # kind: (fts table, trigram table, base table, text column)
    "fact": ("facts_fts", "facts_tri", "facts", "content"),
    "chunk": ("chunks_fts", "chunks_tri", "chunks", "text"),
    "transcript": ("transcript_fts", "transcript_tri", "transcript", "text"),
}


AGENT_NS = "agent:"
"""M7: reserved namespace of per-agent tenants (`memory_tenant` on an agent manifest)."""


@dataclass
class Row:
    kind: str  # fact | chunk | transcript
    id: int
    text: str
    source: str | None
    trust: float
    extra: dict[str, object]


def active(alias: str = "") -> str:
    """SQL predicate for retrievable facts; binds one parameter (`now`)."""
    p = f"{alias}." if alias else ""
    return f"{p}archived_at IS NULL AND ({p}valid_to IS NULL OR {p}valid_to > ?)"


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


def has_cjk(text: str) -> bool:
    return CJK.search(text) is not None


def cjk_terms(raw: str) -> tuple[list[str], list[str]]:
    """(3-grams of runs ≥ 3 chars for the trigram index, short runs for LIKE) of `raw`'s CJK."""
    runs = list(dict.fromkeys(CJK.findall(raw)))
    grams = [r[i : i + 3] for r in runs if len(r) >= 3 for i in range(len(r) - 2)]  # substring trigrams
    return list(dict.fromkeys(grams))[:32], [r for r in runs if len(r) < 3][:8]


class SearchMixin:
    db: sqlite3.Connection
    tenant: str
    dims: int
    vec_enabled: bool
    trigram: bool

    def active_ids(self, ids: list[int]) -> set[int]:
        if not ids:
            return set()
        marks = ",".join("?" * len(ids))
        rows = self.db.execute(
            f"SELECT id FROM facts WHERE tenant_id=? AND id IN ({marks}) AND {active()}",
            (self.tenant, *ids, time.time()),
        ).fetchall()
        return {int(r[0]) for r in rows}

    def _where(self, kind: str, include_archived: bool) -> tuple[str, tuple[Any, ...]]:
        only_active = kind == "fact" and not include_archived
        return (f" AND {active('b')}", (time.time(),)) if only_active else ("", ())

    def fts(self, kind: str, query: str, limit: int, include_archived: bool = False) -> list[int]:
        table, _, base, _ = _TABLES[kind]
        out: list[int] = []
        if self.trigram and has_cjk(query):
            out = self._cjk(kind, query, limit, include_archived)
        q = fts_query(CJK.sub(" ", query) if out else query)
        if q is None:
            return out
        where, extra = self._where(kind, include_archived)
        tenant = " AND b.tenant_id = ?" if kind != "transcript" else ""
        args: tuple[Any, ...] = (q, *((self.tenant,) if tenant else ()), *extra, limit)
        rows = self.db.execute(
            f"SELECT f.rowid FROM {table} f JOIN {base} b ON b.id = f.rowid "
            f"WHERE {table} MATCH ?{tenant}{where} ORDER BY rank LIMIT ?",
            args,
        ).fetchall()
        return list(dict.fromkeys([*out, *(int(r[0]) for r in rows)]))[:limit]

    def _cjk(self, kind: str, query: str, limit: int, include_archived: bool) -> list[int]:
        where, extra = self._where(kind, include_archived)
        if kind != "transcript":
            where, extra = f" AND b.tenant_id = ?{where}", (self.tenant, *extra)
        return self._cjk_rows(kind, query, limit, where, extra)

    def _cjk_rows(self, kind: str, query: str, limit: int, where: str, extra: tuple[Any, ...]) -> list[int]:
        _, tri, base, col = _TABLES[kind]
        long_terms, short = cjk_terms(query)
        out: list[int] = []
        if long_terms:
            q = " OR ".join('"' + t.replace('"', '""') + '"' for t in long_terms)
            rows = self.db.execute(
                f"SELECT f.rowid FROM {tri} f JOIN {base} b ON b.id = f.rowid "
                f"WHERE {tri} MATCH ?{where} ORDER BY rank LIMIT ?",
                (q, *extra, limit),
            ).fetchall()
            out += [int(r[0]) for r in rows]
        for term in short:  # trigram cannot index 1–2 character words
            rows = self.db.execute(
                f"SELECT b.id FROM {base} b WHERE instr(b.{col}, ?) > 0{where} ORDER BY b.id DESC LIMIT ?",
                (term, *extra, limit),
            ).fetchall()
            out += [int(r[0]) for r in rows]
        return list(dict.fromkeys(out))[:limit]

    def knn(
        self,
        kind: str,
        embedding: list[float],
        limit: int,
        include_archived: bool = False,
        model: str | None = None,
    ) -> list[int]:
        if not self.vec_enabled or len(embedding) != self.dims:
            return []
        import sqlite_vec

        table, base = {"fact": ("facts_vec", "facts"), "chunk": ("chunks_vec", "chunks")}[kind]
        only_active = kind == "fact" and not include_archived
        wide = limit * 2 if (only_active or model) else limit
        rows = self.db.execute(
            f"SELECT rowid FROM {table}_{self.dims} WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (sqlite_vec.serialize_float32(embedding), wide),
        ).fetchall()
        ids = [int(r[0]) for r in rows]
        if only_active:  # over-fetch, then drop archived / expired facts
            keep = self.active_ids(ids)
            ids = [i for i in ids if i in keep]
        if model and ids:  # only vectors written by the current embedding model (§24.3)
            marks = ",".join("?" * len(ids))
            same = self.db.execute(
                f"SELECT id FROM {base} WHERE id IN ({marks}) AND embed_model = ?", (*ids, model)
            ).fetchall()
            keep = {int(r[0]) for r in same}
            ids = [i for i in ids if i in keep]
        return ids[:limit]

    def fetch(self, kind: str, ids: list[int]) -> dict[int, Row]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        if kind == "fact":
            rows = self.db.execute(
                "SELECT id, content, category, trust, scope, owner_session, user_id, created_at, "
                f"half_life_days FROM facts WHERE tenant_id=? AND id IN ({marks})",
                (self.tenant, *ids),
            ).fetchall()
            return {
                int(r[0]): Row(
                    "fact",
                    int(r[0]),
                    r[1],
                    r[2],
                    float(r[3]),
                    {"scope": r[4], "owner_session": r[5], "user_id": r[6]}
                    | {"created_at": r[7], "half_life_days": r[8]},
                )
                for r in rows
            }
        rows = self.db.execute(
            "SELECT id, text, source, start_line, end_line, chunk_strategy, span_start, span_end, "
            f"scope, owner_session FROM chunks WHERE tenant_id=? AND id IN ({marks})",
            (self.tenant, *ids),
        ).fetchall()
        return {
            int(r[0]): Row(
                "chunk",
                int(r[0]),
                r[1],
                r[2],
                -1.0,
                {"start": r[3], "end": r[4], "strategy": r[5] or "early"}
                | {"span_start": r[6], "span_end": r[7], "scope": r[8], "owner_session": r[9]},
            )
            for r in rows
        }

    def transcript_tag(self) -> str:
        """M7: `transcript` has no tenant column (and M7 adds none), so an `agent:<slug>` tenant
        tags the `role` of its rows (`agent:<slug>|user`); `local` and team stores skip them."""
        return f"{self.tenant}|" if self.tenant.startswith(AGENT_NS) else ""

    def search_transcript(self, query: str, limit: int, session_id: str | None) -> list[Row]:
        where = " AND b.session_id = ?" if session_id else ""
        extra: tuple[Any, ...] = (session_id,) if session_id else ()
        tag = self.transcript_tag()
        where += " AND substr(b.role, 1, ?) = ?" if tag else " AND substr(b.role, 1, ?) != ?"
        extra += (len(tag), tag) if tag else (len(AGENT_NS), AGENT_NS)
        ids: list[int] = []
        if self.trigram and has_cjk(query):
            ids = self._cjk_rows("transcript", query, limit, where, extra)
        q = fts_query(CJK.sub(" ", query) if ids else query)
        if q is not None:
            rows = self.db.execute(
                "SELECT f.rowid FROM transcript_fts f JOIN transcript b ON b.id = f.rowid "
                f"WHERE transcript_fts MATCH ?{where} ORDER BY rank LIMIT ?",
                (q, *extra, limit),
            ).fetchall()
            ids = list(dict.fromkeys([*ids, *(int(r[0]) for r in rows)]))[:limit]
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        found = {
            int(r[0]): r
            for r in self.db.execute(
                f"SELECT id, text, session_id, role FROM transcript WHERE id IN ({marks})", ids
            ).fetchall()
        }
        return [
            Row("transcript", i, found[i][1], found[i][2], -1.0, {"role": found[i][3][len(tag) :]})
            for i in ids
            if i in found
        ]


__all__ = [
    "AGENT_NS",
    "CJK",
    "STOPWORDS",
    "Row",
    "SearchMixin",
    "active",
    "cjk_terms",
    "fts_query",
    "has_cjk",
]
