"""I5 API: skills hub search/install/remove, scoped harness reloads, `POST /api/lsp/stop`,
`GET /api/memory/usage`, the Sessions `project` column and the public share view."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from pytest_httpx import HTTPXMock

from auoreo.api.app import create_app
from auoreo.core import settings_store
from auoreo.core.services import Services
from auoreo.core.settings import Settings

INDEX = "https://hub.test/index.json"
MD = (
    "---\nname: {name}\ndescription: Use when you need to {name} things carefully and step by step.\n---\n"
    "# {name}\n\nFollow these steps exactly: read the input, plan, act, verify the outcome.\n"
)


@pytest.fixture
def settings(home: Path) -> Settings:
    st = Settings(home=home, gateway="fake", approval_timeout_s=5)
    st.g6.skills.taps = [INDEX]
    return st


@pytest.fixture
async def client(services: Services) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(services.settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


def _index(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=INDEX,
        json={
            "skills": [
                {"name": "tidy-imports", "description": "sort imports", "url": "https://hub.test/t.md"},
                {"name": "lint-docs", "description": "docs linter", "url": "https://hub.test/l.md"},
            ]
        },
    )


async def test_hub_search_install_approve_remove(
    client: httpx.AsyncClient, services: Services, httpx_mock: HTTPXMock
) -> None:
    _index(httpx_mock)
    httpx_mock.add_response(url="https://hub.test/t.md", content=MD.format(name="tidy-imports").encode())
    r = await client.get("/api/skills/hub/search", params={"q": "import"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["taps"] == [INDEX] and [h["name"] for h in body["hits"]] == ["tidy-imports"]
    assert body["hits"][0]["installed"] is None and body["errors"] == []

    r = await client.post("/api/skills/hub/install", json={"name": "tidy-imports"})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "quarantined" and r.json()["integrity"] == "ok"
    assert "tidy-imports" not in services.harness.skills  # quarantine is invisible to agents
    again = await client.get("/api/skills/hub/search")
    assert {h["name"]: h["installed"] for h in again.json()["hits"]}["tidy-imports"] == "quarantined"
    assert (await client.post("/api/skills/hub/install", json={"name": "tidy-imports"})).status_code == 409
    assert (await client.post("/api/skills/hub/install", json={"name": "Bad Name"})).status_code == 422
    bad_tap = await client.post("/api/skills/hub/install", json={"name": "x", "tap": "https://evil.test/i"})
    assert bad_tap.status_code == 400

    assert (await client.post("/api/skills/hub/tidy-imports/approve")).status_code == 200
    assert "tidy-imports" in services.harness.skills
    r = await client.delete("/api/skills/hub/tidy-imports")
    assert r.status_code == 200 and r.json() == {
        "name": "tidy-imports",
        "removed": True,
        "status": "approved",
    }
    assert "tidy-imports" not in services.harness.skills
    assert (await client.delete("/api/skills/hub/tidy-imports")).status_code == 404


async def test_hub_search_reports_tap_errors(client: httpx.AsyncClient, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=INDEX, status_code=503)
    body = (await client.get("/api/skills/hub/search")).json()
    assert body["hits"] == [] and "503" in body["errors"][0]


async def test_scoped_reload(client: httpx.AsyncClient, services: Services) -> None:
    agent = services.settings.home / "agents" / "scoped" / "AGENT.md"
    agent.parent.mkdir(parents=True)
    agent.write_text("---\nname: scoped\ndescription: test agent\n---\nYou are scoped.\n")
    r = await client.post("/api/harness/reload", params={"kind": "agent"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "agent" and body["changes"] == {"agents": {"added": ["scoped"], "removed": []}}
    assert (await client.post("/api/harness/reload", params={"kind": "command"})).json()["changes"] == {
        "commands": {"added": [], "removed": []}
    }
    lsp = (await client.post("/api/harness/reload", params={"kind": "lsp"})).json()
    assert lsp["changes"] == {"lsp": {"stopped": []}} and "next use" in lsp["note"]

    settings_store.write(services.settings.home, {"model": "small"})
    cfg = (await client.post("/api/harness/reload", params={"kind": "model"})).json()
    assert cfg["changes"] == {"config": {"changed": ["model"]}} and "applied live" in cfg["note"]
    assert services.settings.model == "small"  # ENV: applied in place, no restart
    settings_store.write(services.settings.home, {"teams_max_depth": "not-a-number"})  # raw: no validation
    assert (await client.post("/api/harness/reload", params={"kind": "config"})).status_code == 422
    assert (await client.post("/api/harness/reload", params={"kind": "bogus"})).status_code == 422
    full = (await client.post("/api/harness/reload")).json()
    assert full["kind"] is None and "agents" in full["changes"]


class _FakeClient:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


async def test_lsp_stop_endpoint(client: httpx.AsyncClient, services: Services) -> None:
    from auoreo.tools.lsp_client import manager_for

    mgr = manager_for(services)
    a, b = _FakeClient(), _FakeClient()
    mgr.clients[("s1", "python", "/w")] = a  # type: ignore[assignment]
    mgr.clients[("s2", "rust", "/w")] = b  # type: ignore[assignment]
    r = await client.post("/api/lsp/stop", json={"language": "python"})
    assert r.json() == {"stopped": ["s1:python:/w"], "count": 1} and a.closed and not b.closed
    r = await client.post("/api/lsp/stop")
    assert r.json()["count"] == 1 and b.closed and mgr.clients == {}
    assert (await client.post("/api/lsp/stop", json={"bogus": 1})).status_code == 422


async def test_memory_usage_and_compaction_savings(client: httpx.AsyncClient, services: Services) -> None:
    sid = (await services.create_session()).id
    for pre, post in ((10_000, 2_000), (6_000, 3_000)):
        await services.emit(
            sid, "compaction.completed", {"pre_tokens": pre, "post_tokens": post, "archived": 4}
        )
    await services.memory.add_fact("the user prefers tabs", category="preference")  # type: ignore[union-attr]
    body = (await client.get("/api/memory/usage")).json()
    assert body["enabled"] is True and body["facts"] == 1 and body["compactions"] == 2
    assert body["pre_tokens"] == 16_000 and body["post_tokens"] == 5_000
    assert body["saved_ratio"] == pytest.approx(1 - 5_000 / 16_000, abs=1e-3)
    assert body["archived_messages"] == 8 and body["db_bytes"] > 0


async def test_sessions_list_has_project_and_share_view(
    client: httpx.AsyncClient, services: Services
) -> None:
    created = (await client.post("/api/sessions", json={"title": "proj"})).json()
    rows: list[dict[str, Any]] = (await client.get("/api/sessions")).json()
    row = next(r for r in rows if r["id"] == created["id"])
    assert row["project"] == str(services.cwd)
    link = (await client.post(f"/api/sessions/{created['id']}/share")).json()
    shared = await client.get(link["path"])
    assert shared.status_code == 200 and shared.json()["session"]["id"] == created["id"]
