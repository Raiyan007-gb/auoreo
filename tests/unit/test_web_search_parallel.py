"""G4b: web_search Parallel provider (Exa -> Parallel -> SearXNG) + per-call timeout."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from auoreo.core.services import Services
from tests.unit.test_tools_m5b import call, ctx_for

PARALLEL = "https://api.parallel.ai/v1/search"


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.setenv("PARALLEL_API_KEY", "par-test")


async def test_parallel_backend(services: Services, workdir: Path, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url=PARALLEL,
        match_headers={"x-api-key": "par-test"},
        json={
            "search_id": "s_1",
            "results": [
                {"url": "https://p.example/a", "title": "Parallel A", "excerpts": ["one", "two"]},
                {"url": "https://p.example/b", "title": None, "excerpts": []},
            ],
        },
    )
    text, err, meta = await call(
        services, ctx_for(services, workdir), "web_search", {"query": "agent harness", "max_results": 2}
    )
    assert not err and meta["backend"] == "parallel"
    assert text.startswith("- Parallel A — https://p.example/a\n  one two")
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["objective"] == "agent harness" and body["search_queries"] == ["agent harness"]
    assert body["max_results"] == 2 and body["mode"] == "fast"


async def test_exa_failure_falls_to_parallel(
    services: Services, workdir: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXA_API_KEY", "exa-test")
    httpx_mock.add_response(method="POST", url="https://api.exa.ai/search", status_code=402)
    httpx_mock.add_response(
        method="POST", url=PARALLEL, json={"results": [{"url": "https://x.example", "title": "X"}]}
    )
    text, err, meta = await call(services, ctx_for(services, workdir), "web_search", {"query": "q"})
    assert not err and meta["backend"] == "parallel" and "https://x.example" in text


async def test_parallel_errors_are_surfaced(services: Services, workdir: Path, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=PARALLEL, json={"results": [], "errors": [{"message": "quota"}]}
    )
    text, err, _ = await call(services, ctx_for(services, workdir), "web_search", {"query": "q"})
    assert err and "parallel" in text and "quota" in text


async def test_timeout_override_and_cap(services: Services, workdir: Path, httpx_mock: HTTPXMock) -> None:
    seen: list[float] = []

    def timed_out(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"]["read"])
        raise httpx.ReadTimeout("slow", request=request)

    httpx_mock.add_callback(timed_out, method="POST", url=PARALLEL, is_reusable=True)
    ctx = ctx_for(services, workdir)
    text, err, _ = await call(services, ctx, "web_search", {"query": "q", "timeout_s": 45})
    assert err and "parallel: timed out after 45s" in text
    text, err, _ = await call(services, ctx, "web_search", {"query": "q", "timeout_s": 600})
    assert err and "timeout_s" in text  # the schema caps it at 60 s (validation error)
    services.settings.tools.web_search_timeout_s = 7
    text, err, _ = await call(services, ctx, "web_search", {"query": "q"})
    assert "timed out after 7s" in text
    assert seen == [45.0, 7.0]
    m = services.harness.tools["web_search"].manifest
    assert m.parameters["properties"]["timeout_s"]["maximum"] == 60 and m.timeout_ms >= 180_000
