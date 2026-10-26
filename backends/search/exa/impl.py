"""Exa search (`POST https://api.exa.ai/search`, header `x-api-key`).

`contents.maxAgeHours` replaces the deprecated `livecrawl` (None = omit, 0 = always fresh);
`contents.text.maxCharacters` caps inline text (8k default, the rest is spilled by the
executor); `costDollars.total` and `searchTime` are returned so the tool logs them to usage.
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

URL = "https://api.exa.ai/search"


def body(settings: Any, q: SearchQuery) -> dict[str, Any]:
    cfg = settings.i9.search.exa
    contents: dict[str, Any] = {"highlights": True}
    if cfg.text_max_chars:
        contents["text"] = {"maxCharacters": cfg.text_max_chars}
    if cfg.max_age_hours is not None:
        contents["maxAgeHours"] = cfg.max_age_hours
    return {"query": q.query, "type": cfg.type, "numResults": q.n, "contents": contents}


class Backend(SearchBackend):
    name = "exa"
    key_env = "EXA_API_KEY"

    async def search(self, client: httpx.AsyncClient, q: SearchQuery, secret: Secret) -> SearchOutcome:
        res = await egress_send(
            client, "POST", URL, headers={**UA, "x-api-key": self.key(secret)}, json=body(self.settings, q)
        )
        raise_for(self.name, res)
        data = res.json()
        hits = []
        for r in data.get("results") or []:
            snippet = text(r.get("highlights")) or text(r.get("summary")) or text(r.get("text"), 600)
            hits.append(SearchHit(r.get("title") or r.get("url") or "", r.get("url") or "", snippet))
        cost = (data.get("costDollars") or {}).get("total")
        meta = {"searchTime": data.get("searchTime"), "requestId": data.get("requestId")}
        return SearchOutcome(hits, float(cost) if isinstance(cost, int | float) else None, meta)
