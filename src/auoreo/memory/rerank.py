"""Rerank step of the memory pipeline (spec §11.6 / §24.3): top-30 → rerank → top-N.

Fail-open: any exception or a timeout keeps the RRF × trust order, bumps `skipped` and
logs at info; a query never fails because of the reranker. A disabled backend (`none`)
costs nothing: no call, no timer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger(__name__)
WINDOW = 30
"""Hits handed to the reranker (spec §24.3: top-30)."""


class _HasText(Protocol):
    @property
    def text(self) -> str: ...


@dataclass
class RerankStats:
    backend: str = "none"
    calls: int = 0
    skipped: int = 0
    last_ms: float = 0.0
    total_ms: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "rerank_backend": self.backend,
            "rerank_calls": self.calls,
            "rerank_skipped": self.skipped,
            "rerank_ms": round(self.last_ms, 2),
            "rerank_ms_avg": round(self.total_ms / self.calls, 2) if self.calls else 0.0,
        }


async def apply[H: _HasText](
    backend: Any,
    query: str,
    hits: Sequence[H],
    limit: int,
    *,
    top_n: int,
    timeout_s: float,
    stats: RerankStats,
) -> list[H]:
    """Return the hits to keep. `hits` is the full RRF × trust ordering, best first."""
    window = list(hits[:WINDOW])
    if backend is None or not getattr(backend, "enabled", False) or len(window) < 2:
        return window[:limit]
    keep = max(1, min(limit, top_n))
    stats.backend = getattr(backend, "name", type(backend).__name__)
    stats.calls += 1
    started = time.perf_counter()
    try:
        ranked = await asyncio.wait_for(
            backend.rerank(query, [h.text for h in window], keep), timeout=timeout_s
        )
        out: list[H] = []
        seen: set[int] = set()
        for r in ranked:
            if not 0 <= r.index < len(window):
                raise ValueError(f"reranker returned index {r.index} for {len(window)} documents")
            if r.index not in seen:
                seen.add(r.index)
                out.append(window[r.index])
        if not out:
            raise ValueError("reranker returned no results")
        return out[:keep]
    except Exception as exc:  # fail-open (TimeoutError included): keep the RRF order
        stats.skipped += 1
        log.info("rerank skipped (%s): %s", stats.backend, type(exc).__name__ if not str(exc) else exc)
        return window[:limit]
    finally:
        elapsed = (time.perf_counter() - started) * 1000
        stats.last_ms = elapsed
        stats.total_ms += elapsed
