"""Cohere rerank over httpx (`POST https://api.cohere.com/v2/rerank`).

Request `{model, query, documents: [str], top_n}` → `results[{index, relevance_score}]`
(best first, scores in [0, 1]). The key comes only from the secret store (env → keyring
→ `secrets.db`) and is sent as a bearer header; it never appears in errors or logs.
"""

from __future__ import annotations

from collections.abc import Sequence

import httpx

from auoreo.backends import BackendStatus
from auoreo.backends.rerank import Ranked, RerankBackend
from auoreo.voice.cloud import key_status, secret

URL = "https://api.cohere.com/v2/rerank"
KEY = "COHERE_API_KEY"
DEFAULT_MODEL = "rerank-v3.5"


class RerankUnavailable(RuntimeError):
    pass


class Backend(RerankBackend):
    name = "cohere"

    @property
    def model(self) -> str:
        """`settings.rerank_model` when it names a Cohere model (no `org/` prefix, which
        marks the cross-encoder default), else `rerank-v3.5`."""
        chosen = getattr(self.settings, "rerank_model", None)
        return chosen if chosen and "/" not in chosen else DEFAULT_MODEL

    async def check(self) -> BackendStatus:
        return key_status(self.settings, KEY, f"Cohere {self.model}")

    async def rerank(self, query: str, documents: Sequence[str], top_n: int) -> list[Ranked]:
        if not documents:
            return []
        key = secret(self.settings, KEY)
        if not key:
            raise RerankUnavailable(f"{KEY} is not set; run `auoreo secrets set {KEY}`")
        body = {"model": self.model, "query": query, "documents": list(documents), "top_n": top_n}
        timeout = float(getattr(self.settings, "rerank_timeout_s", 2.0))
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(URL, json=body, headers={"Authorization": f"Bearer {key}"})
        if r.status_code != 200:
            try:
                msg = str(r.json().get("message", ""))[:300]
            except ValueError:
                msg = r.text[:300]
            raise RerankUnavailable(f"cohere rerank failed: HTTP {r.status_code}: {msg}")
        results = r.json().get("results") or []
        return [Ranked(int(x["index"]), float(x["relevance_score"])) for x in results][:top_n]
