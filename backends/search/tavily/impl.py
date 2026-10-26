"""Tavily search (`POST https://api.tavily.com/search`, `Authorization: Bearer`).

Opt-in lane (news/finance specialist): `search_depth` basic by default, 8 results,
`chunks_per_source` (advanced only), `include_usage` → credits in the outcome meta (logged to
the usage ledger), `auto_parameters` always off (it may silently double the cost). HTTP
432/433 (plan/pay-as-you-go limit) are typed BUDGET errors.
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

URL = "https://api.tavily.com/search"


def body(settings: Any, q: SearchQuery) -> dict[str, Any]:
    cfg = settings.i9.search.tavily
    out: dict[str, Any] = {
        "query": q.query[:400],
        "search_depth": cfg.search_depth,
        "max_results": q.n,
        "topic": q.topic if q.topic != "general" else cfg.topic,
        "include_usage": True,
        "auto_parameters": False,
    }
    if cfg.search_depth == "advanced":
        out["chunks_per_source"] = cfg.chunks_per_source
    return out


class Backend(SearchBackend):
    name = "tavily"
    key_env = "TAVILY_API_KEY"

    async def search(self, client: httpx.AsyncClient, q: SearchQuery, secret: Secret) -> SearchOutcome:
        res = await egress_send(
            client,
            "POST",
            URL,
            headers={**UA, "Authorization": f"Bearer {self.key(secret)}"},
            json=body(self.settings, q),
        )
        raise_for(self.name, res)
        data = res.json()
        hits = [
            SearchHit(r.get("title") or r.get("url") or "", r.get("url") or "", text(r.get("content")))
            for r in data.get("results") or []
        ]
        meta = {
            "credits": (data.get("usage") or {}).get("credits"),
            "response_time": data.get("response_time"),
        }
        return SearchOutcome(hits, None, meta)
