"""Search backend kind (`backends/search/<name>/`, spec §25.15 L1097).

Unlike most kinds, `web_search` uses an ordered *chain* of search backends
(`settings.i9.search.order`); the first configured one that answers wins. Every backend talks
HTTP through the client the tool hands it (`auoreo.tools.net.egress_client`, i.e. through the
loopback egress proxy for this `net: proxy` tool) and reads its key through the call-scoped
secret getter (never from the child env). Readers (`fetch`) back `web_fetch`'s JS-only /
anti-bot fallback (Jina Reader, Firecrawl scrape).

Typed failures: 402 / Tavily 432-433 → `BUDGET`, 401/403 → `AUTH`, other HTTP errors →
`HTTP`; a provider's `errors[]` are surfaced, never dropped.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from auoreo.backends import BackendStatus

Secret = Callable[[str], "str | None"]
BUDGET_STATUSES = frozenset({402, 432, 433})
UA = {"User-Agent": "auoreo/0.1"}


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str = ""


@dataclass
class SearchOutcome:
    hits: list[SearchHit]
    cost_usd: float | None = None
    """Provider-reported spend for this call (Exa `costDollars.total`); logged to usage."""
    meta: dict[str, Any] = field(default_factory=dict)
    """`searchTime`, Tavily `credits`, Parallel `warnings`/`search_queries`, …"""


@dataclass
class SearchQuery:
    query: str
    n: int = 8
    objective: str | None = None
    topic: str = "general"
    keywords: list[str] = field(default_factory=list)
    """Pre-computed keyword fan-out (Parallel `search_queries`); empty = backend heuristic."""


class SearchError(Exception):
    def __init__(self, provider: str, code: str, message: str) -> None:
        super().__init__(f"{provider}: {code}: {message}"[:400])
        self.provider, self.code, self.message = provider, code, message


def raise_for(provider: str, resp: httpx.Response) -> None:
    """Map an HTTP error to a typed `SearchError` (BUDGET / AUTH / HTTP)."""
    if resp.status_code < 400:
        return
    body = resp.text[:200].replace("\n", " ")
    if resp.status_code in BUDGET_STATUSES:
        raise SearchError(provider, "BUDGET", f"HTTP {resp.status_code} (credits/plan limit): {body}")
    if resp.status_code in {401, 403}:
        raise SearchError(provider, "AUTH", f"HTTP {resp.status_code}: check the API key")
    raise SearchError(provider, "HTTP", f"HTTP {resp.status_code}: {body}")


class SearchBackend(ABC):
    name = "search"
    key_env: str | None = None
    """Secret name of the API key (None: keyless / URL-configured)."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    def configured(self, secret: Secret) -> bool:
        return self.key_env is None or bool(secret(self.key_env))

    def key(self, secret: Secret) -> str:
        return (secret(self.key_env) or "") if self.key_env else ""

    async def check(self) -> BackendStatus:
        need = f"key {self.key_env} (secret store)" if self.key_env else "no key"
        order = ", ".join(self.settings.i9.search.order)
        return BackendStatus(True, f"{self.name}: {need}; web_search order: {order}")

    @abstractmethod
    async def search(self, client: httpx.AsyncClient, q: SearchQuery, secret: Secret) -> SearchOutcome:
        """One search call. Raises `SearchError` / `httpx.HTTPError`."""

    async def fetch(self, client: httpx.AsyncClient, url: str, secret: Secret) -> str | None:
        """Reader: page content as markdown, or None when this backend has no reader."""
        return None


def text(value: Any, limit: int = 2000) -> str:
    if isinstance(value, list):
        value = " ".join(str(v) for v in value)
    return " ".join(str(value or "").split())[:limit]
