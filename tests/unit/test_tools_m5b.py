"""M5b small tools: `str_replace` (edit alias) and `web_search` (backend chain)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.harness.orchestrator import build_spec
from auoreo.tools.context import ToolContext

ALLOW = [Rule(permission="*", pattern="*", action="allow")]


@pytest.fixture(autouse=True)
def _no_host_search_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXA_API_KEY", raising=False)


def ctx_for(svc: Services, workdir: Path) -> ToolContext:
    spec = build_spec(svc.harness, "default", "default", "manual")
    engine = PermissionEngine(list(ALLOW))
    return ToolContext(
        session_id="s1",
        cwd=workdir,
        home=svc.settings.home,
        harness=svc.harness,
        services={"memory": svc.memory, "services": svc, "engine": engine, "spec": spec},
    )


async def call(svc: Services, ctx: ToolContext, name: str, args: dict) -> tuple[str, bool, dict]:
    out = await svc.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)),
        tools=svc.harness.tools,
        ctx=ctx,
        engine=ctx.services["engine"],
    )
    return out.result.content, out.result.is_error, out.result.meta


# ------------------------------------------------------------------ str_replace
async def test_str_replace_is_an_edit_alias(services: Services, workdir: Path) -> None:
    f = workdir / "a.py"
    f.write_text("x = 1\ny = 2\n")
    ctx = ctx_for(services, workdir)
    edit = services.harness.tools["edit"].manifest
    alias = services.harness.tools["str_replace"].manifest
    assert alias.parameters == edit.parameters and alias.permission_name == edit.permission_name == "edit"
    assert alias.exposure == "deferred"
    text, err, _ = await call(
        services, ctx, "str_replace", {"path": "a.py", "old_string": "x", "new_string": "z"}
    )
    assert err and "read" in text  # same read-before-edit guard as edit
    await call(services, ctx, "read", {"path": "a.py"})
    text, err, meta = await call(
        services, ctx, "str_replace", {"path": "a.py", "old_string": "x = 1", "new_string": "x = 9"}
    )
    assert not err and meta["replacements"] == 1
    assert f.read_text() == "x = 9\ny = 2\n"


# ------------------------------------------------------------------- web_search
async def test_web_search_without_backends(services: Services, workdir: Path) -> None:
    text, err, _ = await call(services, ctx_for(services, workdir), "web_search", {"query": "auoreo"})
    assert err and "no search backend configured; use web_fetch on a known URL" in text
    m = services.harness.tools["web_search"].manifest
    assert (m.permission, m.default_action, m.is_read_only, m.is_concurrency_safe) == (
        "websearch",
        "allow",
        True,
        True,
    )


async def test_web_search_exa(
    services: Services, workdir: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXA_API_KEY", "exa-test")
    httpx_mock.add_response(
        method="POST",
        url="https://api.exa.ai/search",
        match_headers={"x-api-key": "exa-test"},
        json={
            "results": [
                {
                    "title": "AUOREO",
                    "url": "https://a.example/x",
                    "highlights": ["filesystem-native", "harness"],
                },
                {"title": "", "url": "https://b.example/y", "text": "second"},
            ]
        },
    )
    text, err, meta = await call(
        services, ctx_for(services, workdir), "web_search", {"query": "auoreo", "max_results": 2}
    )
    assert not err
    assert text == (
        "- AUOREO — https://a.example/x\n  filesystem-native harness\n"
        "- https://b.example/y — https://b.example/y\n  second"
    )
    assert meta["backend"] == "exa"
    assert meta["sources"] == [
        {"title": "AUOREO", "url": "https://a.example/x"},
        {"title": "https://b.example/y", "url": "https://b.example/y"},
    ]
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["query"] == "auoreo" and body["numResults"] == 2


async def test_web_search_falls_back_to_searxng(
    services: Services, workdir: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXA_API_KEY", "exa-test")
    services.settings.searxng_url = "http://searx.local:8888/"
    httpx_mock.add_response(method="POST", url="https://api.exa.ai/search", status_code=500)
    httpx_mock.add_response(
        method="GET",
        url=httpx.URL("http://searx.local:8888/search", params={"q": "python", "format": "json"}),
        json={"results": [{"title": "Python", "url": "https://python.org", "content": "The language"}]},
    )
    text, err, meta = await call(services, ctx_for(services, workdir), "web_search", {"query": "python"})
    assert not err and text == "- Python — https://python.org\n  The language"
    assert meta["backend"] == "searxng"


async def test_web_search_all_backends_failing(
    services: Services, workdir: Path, httpx_mock: HTTPXMock
) -> None:
    services.settings.searxng_url = "http://searx.local:8888"
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    text, err, _ = await call(services, ctx_for(services, workdir), "web_search", {"query": "x"})
    assert err and "all search backends failed" in text and "searxng" in text


def test_researcher_can_search(services: Services) -> None:
    spec = build_spec(services.harness, "researcher", "default", "manual")
    assert "web_search" in {t.name for t in [*spec.tools, *spec.deferred]}
