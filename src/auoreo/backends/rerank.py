"""Rerank backend kind (`backends/rerank/<name>/`, spec §11.6 / §24.3).

The memory pipeline is RRF → ×trust → top-30 → `rerank()` → top-8. Rerank is fail-open:
any exception (model missing, timeout, provider error) keeps the RRF order and bumps a
skip counter; a query never fails because of the reranker.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from auoreo.backends import BackendStatus


@dataclass(frozen=True)
class Ranked:
    index: int
    """Position of the document in the input list."""
    score: float
    """Backend relevance score (higher is better; scales differ per backend)."""


class RerankBackend(ABC):
    name: str = "rerank"
    enabled: bool = True
    """False for the `none` backend: callers skip the call entirely."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @abstractmethod
    async def check(self) -> BackendStatus: ...

    @abstractmethod
    async def rerank(self, query: str, documents: Sequence[str], top_n: int) -> list[Ranked]:
        """Return up to `top_n` entries, best first. May raise; callers fail open."""
