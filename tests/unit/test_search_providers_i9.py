"""I9 (spec §25.15 L1097): search/fetch stack — provider contract tests (pytest_httpx), typed
BUDGET errors, configurable order, 24 h cache + dedup, usage logging, and web_fetch's denial
cache, robots.txt, reader fallback and render-escalation hint."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock
from sqlalchemy import select

from auoreo.backends.search import SearchError, SearchQuery
from auoreo.core.permissions import PermissionEngine, Rule
from auoreo.core.services import Services
from auoreo.core.usage import UsageRow
from auoreo.gateway.types import ToolCall
from auoreo.tools import net
from auoreo.tools.context import ToolContext
from auoreo.tools.search_cache import canonical_url, dedup

KEYS = (
    "EXA_API_KEY",
    "PARALLEL_API_KEY",
    "BRAVE_API_KEY",
    "TAVILY_API_KEY",
    "JINA_API_KEY",
    "FIRECRAWL_API_KEY",
)


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def dns() -> Iterator[None]:
    async def public(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    net.RESOLVER[0] = public
    try:
        yield
    finally:
        net.RESOLVER[0] = net.system_resolve


def backend(services: Services, name: str) -> Any:
    return services.harness.backends_for("search")[name].load()(services.settings)


def secret(name: str) -> str | None:
    return f"{name.lower()}-test"


async def search(services: Services, name: str, q: SearchQuery) -> Any:
    async with httpx.AsyncClient() as client:
        return await backend(services, name).search(client, q, secret)


async def call(
    services: Services, workdir: Path, name: str, args: dict, rules: list[Rule] | None = None
) -> Any:
    ctx = ToolContext(
        session_id="s1", cwd=workdir, home=services.settings.home, services={"services": services}
    )
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine(rules or [Rule(permission="webfetch", pattern="*", action="allow")]),
    )
    return out.result


# ------------------------------------------------------------------ provider contracts
async def test_brave_contract(services: Services, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=httpx.URL(
            "https://api.search.brave.com/res/v1/web/search", params={"q": "rust async", "count": 5}
        ),
        match_headers={"X-Subscription-Token": "brave_api_key-test", "Accept": "application/json"},
        json={
            "web": {
                "results": [{"title": "Tokio", "url": "https://tokio.rs", "description": "async runtime"}]
            }
        },
    )
    out = await search(services, "brave", SearchQuery("rust async", 5))
    assert [(h.title, h.url, h.snippet) for h in out.hits] == [("Tokio", "https://tokio.rs", "async runtime")]


async def test_brave_llm_context_endpoint(services: Services, httpx_mock: HTTPXMock) -> None:
    services.settings.i9.search.brave_endpoint = "llm_context"
    httpx_mock.add_response(
        url=httpx.URL("https://api.search.brave.com/res/v1/llm/context", params={"q": "q", "count": 3}),
        json={"grounding": {"generic": [{"title": "G", "url": "https://g.example", "snippets": ["a", "b"]}]}},
    )
    out = await search(services, "brave", SearchQuery("q", 3))
    assert out.hits[0].snippet == "a b" and out.meta["endpoint"] == "llm_context"


async def test_tavily_contract_and_budget(services: Services, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.tavily.com/search",
        match_headers={"Authorization": "Bearer tavily_api_key-test"},
        json={
            "results": [{"title": "T", "url": "https://t.example", "content": "c"}],
            "usage": {"credits": 1},
        },
    )
    out = await search(services, "tavily", SearchQuery("markets today", 8, topic="news"))
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body == {
        "query": "markets today",
        "search_depth": "basic",
        "max_results": 8,
        "topic": "news",
        "include_usage": True,
        "auto_parameters": False,
    }
    assert out.meta["credits"] == 1 and out.hits[0].url == "https://t.example"
    httpx_mock.add_response(method="POST", url="https://api.tavily.com/search", status_code=432)
    with pytest.raises(SearchError) as info:
        await search(services, "tavily", SearchQuery("x"))
    assert info.value.code == "BUDGET"


async def test_tavily_advanced_sends_chunks(services: Services, httpx_mock: HTTPXMock) -> None:
    services.settings.i9.search.tavily.search_depth = "advanced"
    httpx_mock.add_response(method="POST", url="https://api.tavily.com/search", json={"results": []})
    await search(services, "tavily", SearchQuery("x"))
    assert json.loads(httpx_mock.get_requests()[0].content)["chunks_per_source"] == 3


async def test_jina_search_and_reader_contract(services: Services, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=httpx.URL("https://s.jina.ai/", params={"q": "jina reader", "count": 2}),
        match_headers={"Authorization": "Bearer jina_api_key-test", "Accept": "application/json"},
        json={
            "code": 200,
            "data": [{"title": "Reader", "url": "https://jina.ai/reader", "description": "d"}],
        },
    )
    out = await search(services, "jina", SearchQuery("jina reader", 2))
    assert out.hits[0].title == "Reader"
    httpx_mock.add_response(
        url="https://r.jina.ai/https://spa.example/app",
        match_headers={"X-Respond-With": "markdown", "DNT": "1"},
        json={"code": 200, "data": {"title": "App", "url": "https://spa.example/app", "content": "rendered"}},
    )
    async with httpx.AsyncClient() as client:
        text = await backend(services, "jina").fetch(client, "https://spa.example/app", secret)
    assert text == "# App\n\nrendered"


async def test_firecrawl_contract(services: Services, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.firecrawl.dev/v2/search",
        match_headers={"Authorization": "Bearer firecrawl_api_key-test"},
        json={
            "success": True,
            "data": {"web": [{"url": "https://f.example", "title": "F", "description": "x"}]},
        },
    )
    out = await search(services, "firecrawl", SearchQuery("crawl", 4))
    assert json.loads(httpx_mock.get_requests()[0].content) == {"query": "crawl", "limit": 4}
    assert out.hits[0].url == "https://f.example"
    httpx_mock.add_response(
        method="POST",
        url="https://api.firecrawl.dev/v2/scrape",
        json={"success": True, "data": {"markdown": "# rendered"}},
    )
    async with httpx.AsyncClient() as client:
        assert (
            await backend(services, "firecrawl").fetch(client, "https://spa.example", secret) == "# rendered"
        )
    req = json.loads(httpx_mock.get_requests()[-1].content)
    assert req["formats"] == ["markdown"]
    httpx_mock.add_response(method="POST", url="https://api.firecrawl.dev/v2/search", status_code=402)
    with pytest.raises(SearchError, match="BUDGET"):
        await search(services, "firecrawl", SearchQuery("x"))


async def test_exa_contract_max_age_text_and_cost(services: Services, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.exa.ai/search",
        json={
            "results": [{"title": "E", "url": "https://e.example", "highlights": ["h"]}],
            "costDollars": {"total": 0.007},
            "searchTime": 412.5,
        },
    )
    out = await search(services, "exa", SearchQuery("q", 3))
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["contents"] == {"highlights": True, "text": {"maxCharacters": 8000}, "maxAgeHours": 24}
    assert body["type"] == "auto" and out.cost_usd == 0.007 and out.meta["searchTime"] == 412.5


def test_parallel_keyword_fanout() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "p_impl", Path(__file__).resolve().parents[2] / "backends/search/parallel/impl.py"
    )
    assert spec and spec.loader
    impl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(impl)
    qs = impl.keywords("What are the latest developments in solid state battery manufacturing for EVs?")
    assert 2 <= len(qs) <= 3 and all(1 <= len(q.split()) <= 6 for q in qs)
    assert "solid" in qs[0] and "the" not in qs[0].split()


# ------------------------------------------------------------------ the tool
async def test_order_cache_dedup_and_usage(
    services: Services, workdir: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BRAVE_API_KEY", "b")
    monkeypatch.setenv("EXA_API_KEY", "e")
    services.settings.i9.search.order = ["exa", "brave"]
    httpx_mock.add_response(
        method="POST",
        url="https://api.exa.ai/search",
        json={
            "results": [
                {
                    "title": "Same Page Title Here",
                    "url": "https://www.a.example/x/?utm_source=t",
                    "highlights": ["1"],
                },
                {"title": "Same Page Title Here", "url": "https://a.example/x", "highlights": ["dup"]},
                {"title": "Other", "url": "https://b.example", "highlights": ["2"]},
            ],
            "costDollars": {"total": 0.005},
        },
    )
    res = await call(services, workdir, "web_search", {"query": "dedup me"})
    assert not res.is_error and res.meta["backend"] == "exa" and len(res.meta["sources"]) == 2
    res2 = await call(services, workdir, "web_search", {"query": "Dedup  ME"})  # same normalised query
    assert res2.meta["cached"] is True and "[cached answer from exa]" in res2.content
    assert len(httpx_mock.get_requests()) == 1
    async with services.db.session() as s:
        rows = [r for r in (await s.execute(select(UsageRow))).scalars() if r.run_id == "search"]
    assert rows and rows[0].model == "search:exa" and rows[0].cost_usd == 0.005


async def test_all_budget_failures_are_typed(
    services: Services, workdir: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXA_API_KEY", "e")
    services.settings.i9.search.order = ["exa"]
    httpx_mock.add_response(method="POST", url="https://api.exa.ai/search", status_code=402)
    res = await call(services, workdir, "web_search", {"query": "x"})
    assert res.is_error and res.meta["code"] == "BUDGET" and res.content.startswith("BUDGET:")


def test_canonical_url_and_dedup() -> None:
    assert canonical_url("https://WWW.Example.com/a/?utm_medium=x&id=3#frag") == "https://example.com/a?id=3"
    rows = [
        {"title": "One two three", "url": "https://x/1"},
        {"title": "One two three!", "url": "https://x/2"},
    ]
    assert len(dedup(rows)) == 1


# ------------------------------------------------------------------ web_fetch
async def test_web_fetch_robots_denial_cache_and_hint(
    services: Services, workdir: Path, httpx_mock: HTTPXMock, dns: None
) -> None:
    httpx_mock.add_response(url="https://site.example/robots.txt", text="User-agent: *\nDisallow: /private\n")
    res = await call(services, workdir, "web_fetch", {"url": "https://site.example/private/x"})
    assert res.is_error and "robots.txt" in res.content
    js = "<html><head><script src=app.js></script></head><body><div id=root></div></body></html>"
    httpx_mock.add_response(url="https://site.example/app", text=js, headers={"content-type": "text/html"})
    res = await call(services, workdir, "web_fetch", {"url": "https://site.example/app"})
    assert not res.is_error and "browser_navigate" in res.content and res.meta["render_hint"] == "browser"
    httpx_mock.add_response(url="https://site.example/locked", status_code=403, text="no")
    res = await call(services, workdir, "web_fetch", {"url": "https://site.example/locked"})
    assert res.is_error and res.meta["status"] == 403
    res = await call(services, workdir, "web_fetch", {"url": "https://site.example/other"})
    assert res.is_error and "[skipped]" in res.content and res.meta["denied"] == 403  # no request made


async def test_web_fetch_reader_fallback_for_js_pages(
    services: Services, workdir: Path, httpx_mock: HTTPXMock, dns: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JINA_API_KEY", "jk")
    services.settings.i9.fetch.respect_robots = False
    js = "<html><script>boot()</script><noscript>Please enable JavaScript</noscript></html>"
    httpx_mock.add_response(url="https://spa.example/", text=js, headers={"content-type": "text/html"})
    httpx_mock.add_response(
        url="https://r.jina.ai/https://spa.example/",
        json={"data": {"title": "SPA", "content": "the rendered text"}},
    )
    res = await call(services, workdir, "web_fetch", {"url": "https://spa.example/"})
    assert not res.is_error and res.meta["reader"] == "jina" and "the rendered text" in res.content
    assert "[via jina reader]" in res.content
    # credential-looking URLs are never handed to a third-party reader
    httpx_mock.add_response(
        url="https://spa.example/?token=abc", text=js, headers={"content-type": "text/html"}
    )
    res = await call(services, workdir, "web_fetch", {"url": "https://spa.example/?token=abc"})
    assert "reader" not in res.meta and res.meta["render_hint"] == "browser"
