"""Firecrawl v2: search (`POST /v2/search`) and the extract/scrape lane (`POST /v2/scrape`,
`formats: ["markdown"]`) for JS-only / anti-bot pages. `Authorization: Bearer fc-…`.

`i9.search.firecrawl_base_url` points at a self-hosted instance (AGPL: run it as a separate,
isolated service; nothing is imported here). HTTP 402 (credits) is a typed BUDGET error.
"""

from __future__ import annotations

from typing import Any

import httpx

from auoreo.backends.search import (
    UA,
    SearchBackend,
    SearchError,
    SearchHit,
    SearchOutcome,
    SearchQuery,
    Secret,
    raise_for,
    text,
)
from auoreo.tools.net import egress_send


def rows_of(data: Any) -> list[dict[str, Any]]:
    """`data.web[]` (v2) or a bare `data[]` list (v1-compatible self-hosts)."""
    if isinstance(data, dict):
        return list(data.get("web") or data.get("results") or [])
    return list(data or [])


class Backend(SearchBackend):
    name = "firecrawl"
    key_env = "FIRECRAWL_API_KEY"

    @property
    def base(self) -> str:
        return str(self.settings.i9.search.firecrawl_base_url).rstrip("/")

    def _headers(self, secret: Secret) -> dict[str, str]:
        return {**UA, "Authorization": f"Bearer {self.key(secret)}"}

    async def search(self, client: httpx.AsyncClient, q: SearchQuery, secret: Secret) -> SearchOutcome:
        res = await egress_send(
            client,
            "POST",
            f"{self.base}/v2/search",
            headers=self._headers(secret),
            json={"query": q.query[:400], "limit": q.n},
        )
        raise_for(self.name, res)
        payload = res.json()
        if payload.get("success") is False:
            raise SearchError(self.name, "PROVIDER", str(payload.get("error") or "search failed"))
        hits = [
            SearchHit(r.get("title") or r.get("url") or "", r.get("url") or "", text(r.get("description")))
            for r in rows_of(payload.get("data"))
        ]
        return SearchOutcome(hits, None, {"creditsUsed": payload.get("creditsUsed")})

    async def fetch(self, client: httpx.AsyncClient, url: str, secret: Secret) -> str | None:
        res = await egress_send(
            client,
            "POST",
            f"{self.base}/v2/scrape",
            headers=self._headers(secret),
            json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
        )
        raise_for(self.name, res)
        data = res.json().get("data") or {}
        return data.get("markdown") or None
