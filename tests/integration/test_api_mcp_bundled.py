"""`POST /api/harness/mcp/bundled/{bundle}`: enable a bundled MCP server from the web, the API
twin of `auoreo mcp add --bundle <name>` (writes `<home>/mcp/<name>.yaml`, then reloads)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import yaml
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core.services import Services
from auoreo.core.settings import Settings


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


async def test_enabling_a_bundled_server_writes_the_definition_and_reloads(
    client: httpx.AsyncClient, services: Services, home: Path
) -> None:
    before = {s["name"]: s for s in (await client.get("/api/harness/mcp")).json()}
    assert before["time"]["available"] is False
    assert "mcp add --bundle time" in (before["time"]["unavailable_reason"] or "")

    r = await client.post("/api/harness/mcp/bundled/time")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "time" and body["path"].endswith("time.yaml")
    doc = yaml.safe_load((home / "mcp" / "time.yaml").read_text(encoding="utf-8"))
    assert doc["name"] == "time" and doc["enabled"] is True
    assert body["generation"] == services.harness.generation

    after = {s["name"]: s for s in (await client.get("/api/harness/mcp")).json()}
    assert after["time"]["available"] is not False and after["time"]["source"] == "runtime"


async def test_enabling_twice_or_an_unknown_bundle_is_refused(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/harness/mcp/bundled/time")).status_code == 200
    assert (await client.post("/api/harness/mcp/bundled/time")).status_code == 409
    assert (await client.post("/api/harness/mcp/bundled/nope")).status_code == 404
    assert (await client.post("/api/harness/mcp/bundled/.hidden")).status_code == 404  # never looked up
