"""ACP v1 session methods added by the F7 changelog spike: `session/list`, `session/resume`,
`session/close` (advertised under `agentCapabilities.sessionCapabilities`)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.api.routers import acp
from auoreo.core.services import Services
from auoreo.core.settings import Settings


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", timeout=20
        ) as c,
    ):
        yield c


async def call(c: httpx.AsyncClient, method: str, params: dict[str, Any] | None = None) -> Any:
    r = await c.post("/acp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    assert r.status_code == 200, r.text
    return r.json()


async def new(c: httpx.AsyncClient) -> str:
    return str((await call(c, "session/new", {}))["result"]["sessionId"])


async def test_list_pages_newest_first_with_an_opaque_cursor(
    client: httpx.AsyncClient, services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(acp, "LIST_PAGE", 2)
    sids = [await new(client) for _ in range(3)]
    first = (await call(client, "session/list", {}))["result"]
    assert [s["sessionId"] for s in first["sessions"]] == sids[::-1][:2]
    item = first["sessions"][0]
    assert item["cwd"] == str(services.cwd) and item["title"] == "acp" and "T" in item["updatedAt"]
    second = (await call(client, "session/list", {"cursor": first["nextCursor"]}))["result"]
    assert [s["sessionId"] for s in second["sessions"]] == [sids[0]]
    assert "nextCursor" not in second


async def test_list_cwd_filter_and_bad_cursor(client: httpx.AsyncClient, services: Services) -> None:
    await new(client)
    same = (await call(client, "session/list", {"cwd": str(services.cwd)}))["result"]["sessions"]
    assert len(same) == 1
    assert (await call(client, "session/list", {"cwd": "/elsewhere"}))["result"] == {"sessions": []}
    err = (await call(client, "session/list", {"cursor": "abc"}))["error"]
    assert err["code"] == -32602 and "cursor" in err["message"]


async def test_resume_and_close_require_a_known_session(client: httpx.AsyncClient) -> None:
    sid = await new(client)
    assert (await call(client, "session/resume", {"sessionId": sid, "cwd": "/x"}))["result"] == {}
    assert (await call(client, "session/close", {"sessionId": sid}))["result"] == {}
    for method in ("session/resume", "session/close"):
        err = (await call(client, method, {"sessionId": "nope"}))["error"]
        assert err["code"] == -32602 and "unknown session" in err["message"]
