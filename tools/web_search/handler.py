"""web_search: an ordered chain of search backends (`backends/search/*`), first one that answers.

Order: `settings.i9.search.order` (default exa → parallel → brave → tavily → jina → firecrawl →
searxng); a provider without its key in the secret store (or, for SearXNG, without
`settings.searxng_url`) is skipped. None configured → a clear error telling the model to use
web_fetch on a known URL. All HTTP goes through `egress_client` — this tool is `net: proxy`, so
every connection passes the loopback egress proxy (SSRF floor, allow/deny lists, audit).

- answers are cached 24 h (`topic: news` 1 h) in `<home>/search_cache.db`, deduplicated by
  canonical URL + title simhash; a cached answer says so;
- provider spend (Exa `costDollars`, Tavily credits) goes to the usage ledger (`run_id=search`)
  and `searchTime` to the log; HTTP 402 / 432 / 433 are typed `BUDGET` errors;
- Parallel gets the query as `objective` plus 2–3 keyword `search_queries` (heuristic, or one
  temperature-0 aux call with `i9.search.parallel.keyword_fanout: aux`);
- an explicit failure is reported honestly: no silent reroute beyond the configured chain.
Keys are read through `services.secrets` (scoped to this tool's `requires_env`), never from
the child env. Each hop gets `timeout_s` (default `settings.tools.web_search_timeout_s`, max 60 s).
"""

import asyncio
import logging

import httpx

from auoreo.backends.search import SearchError, SearchQuery
from auoreo.gateway.types import Usage
from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.net import egress_client
from auoreo.tools.search_cache import SearchCache, dedup, query_key

log = logging.getLogger(__name__)
MAX_TIMEOUT_S = 60.0
NO_BACKEND = "no search backend configured; use web_fetch on a known URL"
SNIPPET_CHARS = 300
FANOUT_PROMPT = (
    "Rewrite the search objective as 2 or 3 short keyword queries (3-6 words each), one per line, "
    "no numbering, no quotes."
)


def _snippet(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= SNIPPET_CHARS else text[: SNIPPET_CHARS - 1] + "…"


def _render(rows: list[dict], backend: str, query: str, extra: dict) -> ToolResult:
    if not rows:
        return ToolResult(
            f"no results for {query!r} ({backend})", meta={"backend": backend, "sources": [], **extra}
        )
    lines = [f"- {r['title']} — {r['url']}\n  {_snippet(r['snippet'])}" for r in rows]
    if extra.get("cached"):
        lines.append(f"[cached answer from {backend}]")
    sources = [{"title": r["title"], "url": r["url"]} for r in rows]
    return ToolResult("\n".join(lines), meta={"backend": backend, "sources": sources, **extra})


async def _aux_keywords(svc, query: str) -> list[str]:
    """2–3 keyword queries from one temperature-0 aux call; [] on any failure."""
    from auoreo.gateway.auxiliary import resolve_aux_model
    from auoreo.gateway.types import LLMRequest, TextDelta

    try:
        request = LLMRequest(
            model=resolve_aux_model(svc.settings, "search", gateway=svc.gateway),
            messages=(
                {"role": "system", "content": FANOUT_PROMPT},
                {"role": "user", "content": query[:1000]},
            ),
            tool_choice="none",
            temperature=0,
            max_tokens=60,
            metadata={"purpose": "search_fanout"},
        )

        async def ask() -> str:
            return "".join([i.text async for i in svc.gateway.stream(request) if isinstance(i, TextDelta)])

        text = await asyncio.wait_for(ask(), 8)
    except Exception:  # aux is an optimisation; the heuristic covers every failure
        return []
    lines = [" ".join(x.strip(" -*0123456789.\"'").split()) for x in text.splitlines()]
    out = [x[:200] for x in lines if 1 <= len(x.split()) <= 8]
    return out[:3] if len(out) >= 2 else []


async def _record_spend(svc, ctx: ToolContext, provider: str, cost: float | None, meta: dict) -> None:
    credits = meta.get("credits")
    if cost is None and credits is None:
        return
    ledger = getattr(getattr(getattr(svc, "coordinator", None), "runner", None), "usage", None)
    if ledger is None:
        return
    tags = (f"credits={credits}",) if credits is not None else ()
    try:
        usage = Usage(cost_usd=cost, estimated=cost is None, tags=tags)
        await ledger.record(ctx.session_id, "search", f"search:{provider}", usage)
    except Exception:
        log.warning("could not record %s search spend", provider, exc_info=True)


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    svc = ctx.services.get("services")
    secrets = getattr(svc, "secrets", None)
    settings = getattr(svc, "settings", None)
    harness = getattr(svc, "harness", None)
    if settings is None or harness is None:
        raise ToolError(NO_BACKEND)
    query, n, topic = args["query"], int(args.get("max_results", 8)), args.get("topic", "general")

    def secret(name: str) -> str | None:
        return secrets.get(name) if secrets is not None else None

    cfg = settings.i9.search
    cache = SearchCache(settings.home)
    key = query_key(query, n, topic)
    ttl = cfg.news_cache_ttl_s if topic == "news" else cfg.cache_ttl_s
    if ttl and (hit := await cache.get(key)) is not None:
        return _render(hit["rows"], hit["provider"], query, {"cached": True, **hit.get("meta", {})})
    available = harness.backends_for("search")
    chain = []
    for name in cfg.order:
        if (bdef := available.get(name)) is not None and (backend := bdef.load()(settings)).configured(
            secret
        ):
            chain.append(backend)
    if not chain:
        raise ToolError(NO_BACKEND)
    fanout: list[str] = []
    if cfg.parallel.keyword_fanout == "aux" and any(b.name == "parallel" for b in chain):
        fanout = await _aux_keywords(svc, query)
    errors: list[str] = []
    budget = False
    default = float(getattr(getattr(settings, "tools", None), "web_search_timeout_s", 20.0))
    timeout = min(float(args.get("timeout_s") or default), MAX_TIMEOUT_S)
    q = SearchQuery(query=query, n=n, objective=query, topic=topic, keywords=fanout)
    async with egress_client(timeout_s=timeout, follow_redirects=True) as client:
        for backend in chain:
            try:
                out = await backend.search(client, q, secret)
            except httpx.TimeoutException:
                errors.append(f"{backend.name}: timed out after {timeout:g}s")
                continue
            except SearchError as exc:
                budget = budget or exc.code == "BUDGET"
                errors.append(str(exc)[:300])
                continue
            except (httpx.HTTPError, ValueError, ToolError) as exc:
                errors.append(f"{backend.name}: {type(exc).__name__}: {exc}"[:300])
                continue
            rows = dedup([{"title": h.title, "url": h.url, "snippet": h.snippet} for h in out.hits])[:n]
            if out.cost_usd is not None or out.meta.get("searchTime") is not None:
                log.info(
                    "search %s cost=%s searchTime=%s", backend.name, out.cost_usd, out.meta.get("searchTime")
                )
            await _record_spend(svc, ctx, backend.name, out.cost_usd, out.meta)
            meta = {k: v for k, v in out.meta.items() if v is not None}
            if out.cost_usd is not None:
                meta["cost_usd"] = out.cost_usd
            if rows:
                await cache.put(key, backend.name, {"rows": rows, "meta": meta}, ttl)
            return _render(rows, backend.name, query, meta)
    message = "all search backends failed: " + "; ".join(errors)
    if budget:
        return ToolResult(f"BUDGET: {message}", is_error=True, meta={"code": "BUDGET"})
    raise ToolError(message)
