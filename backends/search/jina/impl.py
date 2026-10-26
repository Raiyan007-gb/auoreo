"""Jina: search (`GET https://s.jina.ai/?q=`) and Reader (`GET https://r.jina.ai/<url>`).

Both need `JINA_API_KEY` (anonymous Reader access exists but is rate-limited; the key is the
gate here). JSON responses (`Accept: application/json`) wrap the payload as `{"data": …}`.
The Reader honours robots.txt (`X-Robots-Txt`) and sends `DNT: 1`; `web_fetch` never hands it
a URL that carries credentials or token-like query parameters.
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

SEARCH = "https://s.jina.ai/"
READER = "https://r.jina.ai/"


def _data(payload: Any) -> Any:
    return payload.get("data", payload) if isinstance(payload, dict) else payload


class Backend(SearchBackend):
    name = "jina"
    key_env = "JINA_API_KEY"

    def _headers(self, secret: Secret) -> dict[str, str]:
        return {**UA, "Accept": "application/json", "Authorization": f"Bearer {self.key(secret)}"}

    async def search(self, client: httpx.AsyncClient, q: SearchQuery, secret: Secret) -> SearchOutcome:
        res = await egress_send(
            client,
            "GET",
            SEARCH,
            params={"q": q.query[:400], "count": min(q.n, 20)},
            headers={**self._headers(secret), "X-Respond-With": "no-content"},
        )
        raise_for(self.name, res)
        rows = _data(res.json()) or []
        hits = [
            SearchHit(
                r.get("title") or r.get("url") or "",
                r.get("url") or "",
                text(r.get("description") or r.get("content")),
            )
            for r in rows
            if isinstance(r, dict)
        ]
        return SearchOutcome(hits, None, {"tokens": res.headers.get("x-usage-tokens")})

    async def fetch(self, client: httpx.AsyncClient, url: str, secret: Secret) -> str | None:
        headers = {
            **self._headers(secret),
            "X-Respond-With": "markdown",
            "X-Robots-Txt": "auoreo",
            "DNT": "1",
        }
        res = await egress_send(client, "GET", READER + url, headers=headers)
        raise_for(self.name, res)
        data = _data(res.json())
        content = data.get("content") if isinstance(data, dict) else None
        if not content:
            return None
        title = data.get("title") or ""
        return f"# {title}\n\n{content}" if title else str(content)
