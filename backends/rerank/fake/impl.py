"""Token-overlap reranker: share of distinct query terms found in the document.

Deterministic and dependency-free. Ties keep the incoming (RRF) order, so a document set
with no lexical signal comes back unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from auoreo.backends import BackendStatus
from auoreo.backends.rerank import Ranked, RerankBackend

_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = """a an and are as at be by do does for from has have how i in is it my of on or
the to was what when where which who why with you your"""
_STOP = frozenset(_STOPWORDS.split())


def terms(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1}


class Backend(RerankBackend):
    name = "fake"

    async def check(self) -> BackendStatus:
        return BackendStatus(True, "token-overlap reranker (deterministic, no model)")

    async def rerank(self, query: str, documents: Sequence[str], top_n: int) -> list[Ranked]:
        q = terms(query)
        scored = [Ranked(i, len(q & terms(doc)) / len(q) if q else 0.0) for i, doc in enumerate(documents)]
        scored.sort(key=lambda r: (-r.score, r.index))
        return scored[:top_n]
