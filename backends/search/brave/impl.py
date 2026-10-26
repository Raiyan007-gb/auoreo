"""Brave Search (`GET https://api.search.brave.com/res/v1/web/search`, `X-Subscription-Token`).

`i9.search.brave_endpoint: llm_context` switches to `/res/v1/llm/context` (RAG grounding;
parsed defensively: `grounding.generic[]` or `web.results[]`). `count` is capped at 20.
"""

from __future__ import annotations

from typing import Any

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

BASE = "https://api.search.brave.com/res/v1"
PATHS = {"web": "/web/search", "llm_context": "/llm/context"}


def rows_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    grounding = data.get("grounding") or {}
    return list((data.get("web") or {}).get("results") or grounding.get("generic") or [])


class Backend(SearchBackend):
    name = "brave"
    key_env = "BRAVE_API_KEY"

    async def search(self, client: httpx.AsyncClient, q: SearchQuery, secret: Secret) -> SearchOutcome:
        endpoint = self.settings.i9.search.brave_endpoint
        res = await egress_send(
            client,
            "GET",
            BASE + PATHS[endpoint],
            params={"q": q.query[:400], "count": min(q.n, 20)},
            headers={**UA, "Accept": "application/json", "X-Subscription-Token": self.key(secret)},
        )
        raise_for(self.name, res)
        hits = []
        for r in rows_of(res.json()):
            snippet = text(r.get("description")) or text(r.get("snippets")) or text(r.get("extra_snippets"))
            hits.append(SearchHit(text(r.get("title"), 300) or r.get("url", ""), r.get("url") or "", snippet))
        return SearchOutcome(hits, None, {"endpoint": endpoint})
