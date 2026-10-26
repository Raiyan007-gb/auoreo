"""web_search: a chain of backends, first configured one that answers wins.

1. Exa      — when `EXA_API_KEY` is in the secret store (env → keyring → secrets.db).
2. Parallel — when `PARALLEL_API_KEY` is in the secret store (`POST /v1/search`, mode fast;
   its `errors[]` are surfaced, never dropped).
3. SearXNG  — when `settings.searxng_url` is set (`GET <url>/search?format=json`).
None configured → a clear error telling the model to use web_fetch on a known URL.
Keys are read through `services.secrets`, never from the tool's child env. Each hop gets
`timeout_s` (default `settings.tools.web_search_timeout_s`, at most 60 s).
"""

import httpx

from auoreo.tools.context import ToolContext, ToolError, ToolResult

EXA_URL = "https://api.exa.ai/search"
PARALLEL_URL = "https://api.parallel.ai/v1/search"
MAX_TIMEOUT_S = 60.0
NO_BACKEND = "no search backend configured; use web_fetch on a known URL"
SNIPPET_CHARS = 300
UA = {"User-Agent": "auoreo/0.1"}


def _snippet(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= SNIPPET_CHARS else text[: SNIPPET_CHARS - 1] + "…"


def _row(title: str | None, url: str | None, snippet: str | None) -> dict:
    return {"title": title or url or "", "url": url or "", "snippet": snippet or ""}


async def _exa(client: httpx.AsyncClient, key: str, query: str, n: int) -> list[dict]:
    res = await client.post(
        EXA_URL,
        headers={**UA, "x-api-key": key},
        json={"query": query, "type": "auto", "numResults": n, "contents": {"highlights": True}},
    )
    res.raise_for_status()
    out = []
    for r in res.json().get("results") or []:
        snippet = " ".join(r.get("highlights") or []) or r.get("summary") or r.get("text") or ""
        out.append(_row(r.get("title"), r.get("url"), snippet))
    return out


async def _parallel(client: httpx.AsyncClient, key: str, query: str, n: int) -> list[dict]:
    res = await client.post(
        PARALLEL_URL,
        headers={**UA, "x-api-key": key},
        json={
            "objective": query,
            "search_queries": [query[:200]],
            "mode": "fast",
            "max_results": n,
            "advanced_settings": {"excerpt_settings": {"max_chars_per_result": 1500}},
        },
    )
    res.raise_for_status()
    body = res.json()
    rows = body.get("results") or []
    if not rows and body.get("errors"):
        raise ValueError(f"parallel errors: {body['errors']}"[:300])
    return [_row(r.get("title"), r.get("url"), " ".join(r.get("excerpts") or [])) for r in rows]


async def _searxng(client: httpx.AsyncClient, base: str, query: str, n: int) -> list[dict]:
    res = await client.get(f"{base.rstrip('/')}/search", params={"q": query, "format": "json"}, headers=UA)
    res.raise_for_status()
    rows = (res.json().get("results") or [])[:n]
    return [_row(r.get("title"), r.get("url"), r.get("content")) for r in rows]


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    svc = ctx.services.get("services")
    secrets = getattr(svc, "secrets", None)
    settings = getattr(svc, "settings", None)
    query, n = args["query"], int(args.get("max_results", 8))
    chain = []
    if secrets is not None and (key := secrets.get("EXA_API_KEY")):
        chain.append(("exa", lambda c: _exa(c, key, query, n)))
    if secrets is not None and (pkey := secrets.get("PARALLEL_API_KEY")):
        chain.append(("parallel", lambda c: _parallel(c, pkey, query, n)))
    if settings is not None and (base := getattr(settings, "searxng_url", None)):
        chain.append(("searxng", lambda c: _searxng(c, base, query, n)))
    if not chain:
        raise ToolError(NO_BACKEND)
    errors: list[str] = []
    default = float(getattr(getattr(settings, "tools", None), "web_search_timeout_s", 20.0))
    timeout = min(float(args.get("timeout_s") or default), MAX_TIMEOUT_S)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for name, call in chain:
            try:
                results = (await call(client))[:n]
            except httpx.TimeoutException:
                errors.append(f"{name}: timed out after {timeout:g}s")
                continue
            except (httpx.HTTPError, ValueError) as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}"[:300])
                continue
            if not results:
                return ToolResult(f"no results for {query!r} ({name})", meta={"backend": name, "sources": []})
            lines = [f"- {r['title']} — {r['url']}\n  {_snippet(r['snippet'])}" for r in results]
            sources = [{"title": r["title"], "url": r["url"]} for r in results]
            return ToolResult("\n".join(lines), meta={"backend": name, "sources": sources})
    raise ToolError("all search backends failed: " + "; ".join(errors))
