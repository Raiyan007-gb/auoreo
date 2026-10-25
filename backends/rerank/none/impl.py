"""Pass-through reranker (keeps the incoming order)."""

from __future__ import annotations

from collections.abc import Sequence

from auoreo.backends import BackendStatus
from auoreo.backends.rerank import Ranked, RerankBackend


class Backend(RerankBackend):
    name = "none"
    enabled = False

    async def check(self) -> BackendStatus:
        return BackendStatus(True, "no reranking (RRF × trust order)")

    async def rerank(self, query: str, documents: Sequence[str], top_n: int) -> list[Ranked]:
        return [Ranked(i, 1.0 - i / max(1, len(documents))) for i in range(min(top_n, len(documents)))]
