"""Parallel search (`POST https://api.parallel.ai/v1/search`, header `x-api-key`).

Modes `advanced|basic|turbo` (remapped from agentic/fast/one-shot; `fast` kept). The request
carries the natural-language `objective` plus 2–3 short keyword `search_queries` derived from
it (heuristic by default, one temperature-0 aux call when `keyword_fanout: aux`). Tuning stays
out of the tool schema. `errors[]`/`warnings` are inspected, never dropped.
"""

from __future__ import annotations

import re
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

URL = "https://api.parallel.ai/v1/search"
STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "should",
        "the",
        "this",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
        "about",
        "into",
        "than",
        "then",
        "there",
        "these",
        "those",
        "please",
        "find",
        "search",
        "show",
        "tell",
        "latest",
    ]
)


def keywords(objective: str) -> list[str]:
    """2–3 short keyword queries (3–6 words each) from a natural-language objective."""
    words = [w for w in re.findall(r"[\w][\w.+#'-]*", objective) if w.lower() not in STOP]
    if not words:
        return [objective.strip()[:200]]
    out = [" ".join(words[:6])]
    if len(words) > 3:
        out.append(" ".join(sorted(dict.fromkeys(words), key=len, reverse=True)[:4]))
    if len(words) > 6:
        out.append(" ".join(words[-5:]))
    return list(dict.fromkeys(q[:200] for q in out))[:3]


def body(settings: Any, q: SearchQuery, queries: list[str]) -> dict[str, Any]:
    cfg = settings.i9.search.parallel
    return {
        "objective": q.objective or q.query,
        "search_queries": queries,
        "mode": cfg.mode,
        "advanced_settings": {
            "max_results": q.n,
            "excerpt_settings": {"max_chars_per_result": cfg.max_chars_per_result},
        },
    }


class Backend(SearchBackend):
    name = "parallel"
    key_env = "PARALLEL_API_KEY"

    async def search(self, client: httpx.AsyncClient, q: SearchQuery, secret: Secret) -> SearchOutcome:
        queries = q.keywords or keywords(q.objective or q.query)
        res = await egress_send(
            client,
            "POST",
            URL,
            headers={**UA, "x-api-key": self.key(secret)},
            json=body(self.settings, q, queries),
        )
        raise_for(self.name, res)
        data = res.json()
        rows = data.get("results") or []
        errors = data.get("errors") or []
        if not rows and errors:
            raise SearchError(self.name, "PROVIDER", f"errors: {errors}"[:300])
        hits = [
            SearchHit(r.get("title") or r.get("url") or "", r.get("url") or "", text(r.get("excerpts")))
            for r in rows
        ]
        meta = {"search_queries": queries, "mode": self.settings.i9.search.parallel.mode}
        if errors or data.get("warnings"):
            meta["warnings"] = [*map(str, errors), *map(str, data.get("warnings") or [])][:5]
        return SearchOutcome(hits, None, meta)
