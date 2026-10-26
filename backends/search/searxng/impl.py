"""SearXNG (`GET <settings.searxng_url>/search?format=json`). Keyless; configured by URL.
A loopback/LAN instance is reachable through the egress proxy because its host is added to
the proxy's private allow-list automatically."""

from __future__ import annotations

import httpx

from auoreo.backends.search import (
    UA,
    SearchBackend,
    SearchHit,
    SearchOutcome,
    SearchQuery,
    Secret,
    raise_for,
    text,
)
from auoreo.tools.net import egress_send


class Backend(SearchBackend):
    name = "searxng"

    def configured(self, secret: Secret) -> bool:
        return bool(getattr(self.settings, "searxng_url", None))

    async def search(self, client: httpx.AsyncClient, q: SearchQuery, secret: Secret) -> SearchOutcome:
        base = str(self.settings.searxng_url).rstrip("/")
        res = await egress_send(
            client, "GET", f"{base}/search", params={"q": q.query, "format": "json"}, headers=UA
        )
        raise_for(self.name, res)
        rows = (res.json().get("results") or [])[: q.n]
        hits = [
            SearchHit(r.get("title") or r.get("url") or "", r.get("url") or "", text(r.get("content")))
            for r in rows
        ]
        return SearchOutcome(hits)
