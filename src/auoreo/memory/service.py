"""Async memory facade: hybrid retrieval (RRF k=60), injection block, writes.

Retrieval pipeline (spec §11.6): trivial → skip; FTS ×3 + KNN ×3 per kind → reciprocal
rank fusion → × trust (facts below `min_trust` dropped) → top-30 → rerank (selected
`backends/rerank/` backend, fail-open; see memory/rerank.py) → top-N with citations.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from auoreo.memory import ledger
from auoreo.memory.chunking import chunk_text
from auoreo.memory.contradiction import MIN_SHARED, contradicts, top_terms
from auoreo.memory.files import read_capped
from auoreo.memory.rerank import RerankStats
from auoreo.memory.rerank import apply as rerank_hits
from auoreo.memory.store import MemoryStore, Row, fts_query

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)
_MEMORY_TAG = "memory-context"


@dataclass(frozen=True)
class Hit:
    kind: str
    id: int
    text: str
    score: float
    source: str | None
    citation: str


def is_trivial(query: str) -> bool:
    return fts_query(query) is None or len(query.strip()) < 4


def rrf(rankings: list[list[tuple[str, int]]], k: int) -> dict[tuple[str, int], float]:
    scores: dict[tuple[str, int], float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return scores


def scrub(text: str) -> str:
    """Strip memory-context blocks that leak into user input or model output (§11.7)."""
    import re

    return re.sub(rf"<{_MEMORY_TAG}>.*?</{_MEMORY_TAG}>", "", text, flags=re.S)


class MemoryService:
    def __init__(self, services: Services, store: MemoryStore) -> None:
        self.s = services
        self.store = store
        self.cfg = services.settings.memory
        self._lock = asyncio.Lock()
        self.reranker: Any = None
        """Override for the rerank backend (eval/tests); None → `services.backend("rerank")`."""
        self.rerank_stats = RerankStats()

    def _reranker(self) -> Any:
        if self.reranker is not None:
            return self.reranker
        try:
            return self.s.backend("rerank")
        except Exception:  # no rerank backend discovered / failed to load: RRF order
            return None

    @classmethod
    async def open(cls, services: Services) -> MemoryService:
        cfg = services.settings.memory
        store = await asyncio.to_thread(MemoryStore, services.settings.home / "memory.db", cfg.dims)
        return cls(services, store)

    def close(self) -> None:
        self.store.close()

    async def _run[**P, T](self, fn: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        """Run a blocking store call in a worker thread under the single-writer lock."""
        async with self._lock:
            return await asyncio.to_thread(fn, *args, **kwargs)

    async def _embed(self, texts: list[str]) -> list[list[float]] | None:
        if not self.store.vec_enabled:
            return None
        try:
            return await self.s.gateway.embed(self.cfg.embed_model, texts, self.cfg.dims)
        except Exception as exc:  # vector arm is optional: fail open
            log.info("embedding unavailable, using FTS only: %s", exc)
            return None

    # -------------------------------------------------------------- writes
    async def add_fact(self, content: str, category: str = "user", source: str = "agent") -> tuple[int, bool]:
        content = scrub(content).strip()[:2000]
        emb = await self._embed([content])
        fid, created = await self._run(
            self.store.add_fact,
            content,
            category,
            self.cfg.default_trust,
            emb[0] if emb else None,
            self.cfg.embed_model,
            source,
        )
        if created:
            try:
                await self.flag_contradictions(fid, content)
            except Exception:  # advisory only: never fail the write
                log.exception("contradiction check failed for fact %s", fid)
        return fid, created

    async def flag_contradictions(self, fact_id: int, content: str) -> list[int]:
        """Flag (never overwrite) active facts the new one may contradict (§24.3)."""
        terms = top_terms(content)
        if len(terms) < MIN_SHARED:
            return []
        ids = [i for i in await self._run(self.store.fts, "fact", " ".join(terms), 20) if i != fact_id]
        rows = await self._run(self.store.fetch, "fact", ids)
        hits = [i for i in ids if i in rows and contradicts(content, rows[i].text)]
        for existing in hits:
            await self.s.emit("server", "memory.contradiction", {"new_id": fact_id, "existing_id": existing})
        ledger.append(
            self.s.settings.home,
            [
                {
                    **ledger.entry("curator", "flag_contradiction", fact_id, "possible contradiction"),
                    "existing_id": e,
                }
                for e in hits
            ],
        )
        return hits

    async def replace_fact(self, fact_id: int, content: str) -> int | None:
        """Supersede `fact_id` with `content`; returns the new row's id (None if not active)."""
        content = scrub(content).strip()[:2000]
        emb = await self._embed([content])
        return await self._run(self.store.replace_fact, fact_id, content, emb[0] if emb else None)

    async def forget(self, fact_id: int, actor: str = "user", reason: str = "forgotten on request") -> bool:
        """Archive (never delete) a fact; ledger + audit."""
        ok = await self._run(self.store.archive_fact, fact_id)
        if ok:
            ledger.append(self.s.settings.home, [ledger.entry(actor, "forget", fact_id, reason)])
            await self.s.audit.record(
                actor=actor, action="memory.forget", reason=reason, detail={"fact_id": fact_id}
            )
        return ok

    async def purge(self, fact_id: int, actor: str = "user") -> bool:
        """Hard delete — only the CLI (`memory purge --yes`) calls this."""
        ok = await self._run(self.store.purge_fact, fact_id)
        if ok:
            ledger.append(self.s.settings.home, [ledger.entry(actor, "purge", fact_id, "purged via CLI")])
            await self.s.audit.record(actor=actor, action="memory.purge", detail={"fact_id": fact_id})
        return ok

    async def pin(self, fact_id: int, pinned: bool = True, actor: str = "user") -> bool:
        ok = await self._run(self.store.set_pinned, fact_id, pinned)
        if ok:
            action = "pin" if pinned else "unpin"
            ledger.append(self.s.settings.home, [ledger.entry(actor, action, fact_id, f"{action}ned")])
        return ok

    async def feedback(self, fact_id: int, helpful: bool) -> float | None:
        """Trust feedback: +0.05 when helpful, −0.10 when not (clamped to 0–1)."""
        return await self._run(self.store.bump_trust, fact_id, 0.05 if helpful else -0.10)

    async def get_fact(self, fact_id: int) -> dict[str, Any] | None:
        return await self._run(self.store.get_fact, fact_id)

    async def find_facts(self, substring: str, include_archived: bool = False) -> list[tuple[int, str]]:
        return await self._run(self.store.find_facts, substring, include_archived=include_archived)

    async def ingest(self, text: str, source: str, doc_id: str | None = None) -> int:
        """Chunk + embed + store a document. Returns the number of new chunks."""
        doc_id = doc_id or uuid.uuid4().hex
        model = self.s.settings.model or "default"
        chunks = chunk_text(
            text,
            self.cfg.chunk_tokens,
            self.cfg.chunk_overlap,
            lambda t: self.s.gateway.count_tokens(model, t),
        )
        embs = await self._embed([c.text for c in chunks]) if chunks else None
        added = 0
        for i, c in enumerate(chunks):
            emb = embs[i] if embs else None
            if await self._run(
                self.store.add_chunk,
                doc_id,
                source,
                c.start_line,
                c.end_line,
                c.text,
                c.tokens,
                emb,
                self.cfg.embed_model,
            ):
                added += 1
        return added

    async def record_message(self, session_id: str, role: str, text: str) -> None:
        if text.strip():
            await self._run(self.store.add_transcript, session_id, role, scrub(text)[:20_000])

    # --------------------------------------------------------------- reads
    async def search(
        self,
        query: str,
        limit: int = 8,
        kinds: tuple[str, ...] = ("fact", "chunk"),
        include_archived: bool = False,
    ) -> list[Hit]:
        if is_trivial(query):
            return []
        wide = max(limit * 3, 10)
        emb = await self._embed([query])
        rankings: list[list[tuple[str, int]]] = []
        for kind in kinds:
            fts = await self._run(self.store.fts, kind, query, wide, include_archived)
            rankings.append([(kind, i) for i in fts])
            if emb:
                knn = await self._run(self.store.knn, kind, emb[0], wide, include_archived)
                rankings.append([(kind, i) for i in knn])
        fused = rrf(rankings, self.cfg.rrf_k)
        by_kind: dict[str, list[int]] = {}
        for kind, rid in fused:
            by_kind.setdefault(kind, []).append(rid)
        rows: dict[tuple[str, int], Row] = {}
        for kind, ids in by_kind.items():
            for rid, fetched in (await self._run(self.store.fetch, kind, ids)).items():
                rows[(kind, rid)] = fetched
        hits: list[Hit] = []
        for key, score in fused.items():
            row = rows.get(key)
            if row is None:
                continue
            trust = row.trust if row.kind == "fact" else self.cfg.default_trust
            if row.kind == "fact" and trust < self.cfg.min_trust:
                continue
            cite = (
                f"fact:{row.id}"
                if row.kind == "fact"
                else f"{row.source}:{row.extra.get('start')}-{row.extra.get('end')}"
            )
            hits.append(Hit(row.kind, row.id, row.text, score * trust, row.source, cite))
        hits.sort(key=lambda h: h.score, reverse=True)
        settings = self.s.settings
        top = await rerank_hits(  # top-30 window → rerank (fail-open) → top-N
            self._reranker(),
            query,
            hits,
            limit,
            top_n=settings.rerank_top_n,
            timeout_s=settings.rerank_timeout_s,
            stats=self.rerank_stats,
        )
        used = [h.id for h in top if h.kind == "fact"]
        if used:
            try:  # usage feeds the curator; best effort
                await self._run(self.store.bump_use, used)
            except Exception:
                log.exception("could not record fact usage")
        return top

    async def search_sessions(self, query: str, limit: int = 8, session_id: str | None = None) -> list[Hit]:
        rows = await self._run(self.store.search_transcript, query, limit, session_id)
        return [Hit("transcript", r.id, r.text, 0.0, r.source, f"session:{r.source}") for r in rows]

    async def context_block(self, query: str) -> str:
        """Memory text for the per-turn context snapshot (files + recalled items)."""
        home = self.s.settings.home
        parts: list[str] = []
        mem = read_capped(home, "MEMORY.md", self.cfg.memory_char_limit)
        user = read_capped(home, "USER.md", self.cfg.user_char_limit)
        if mem:
            parts.append(mem)
        if user:
            parts.append(user)
        hits = await self.search(query, limit=8)
        if hits:
            budget = self.cfg.auto_inject_tokens * 4
            lines, used = [], 0
            for h in hits:
                line = f"- [{h.citation}] {h.text.strip()}"
                if used + len(line) > budget:
                    break
                lines.append(line)
                used += len(line)
            n_f = sum(1 for h in hits[: len(lines)] if h.kind == "fact")
            parts.append(
                f"🧠 memory — recalled {n_f} facts + {len(lines) - n_f} chunks\n"
                f"<{_MEMORY_TAG}>\n" + "\n".join(lines) + f"\n</{_MEMORY_TAG}>"
            )
        return "\n\n".join(parts)

    async def stats(self) -> dict[str, object]:
        counts = await self._run(self.store.counts)
        self.rerank_stats.backend = getattr(self._reranker(), "name", None) or "none"
        return {
            **counts,
            "vector": self.store.vec_enabled,
            "dims": self.cfg.dims,
            **self.rerank_stats.as_dict(),
        }
