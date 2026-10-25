"""`GET /api/rtc/ice` (F5): static ICE servers, plus ephemeral TURN REST credentials when
`turn_secret_ref` + `turn_urls` are set; never the secret; never cacheable; auth required."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core.services import Services
from auoreo.rtc.turn import turn_credentials

SECRET = "ice-api-shared-secret-0123456789"


@pytest.fixture
async def client(services: Services) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(services.settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", timeout=20
        ) as c,
    ):
        yield c


async def test_static_only_by_default(client: httpx.AsyncClient, services: Services) -> None:
    services.settings.rtc_ice_servers = ["stun:stun.example.com:3478"]
    r = await client.get("/api/rtc/ice")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "iceServers": [{"urls": ["stun:stun.example.com:3478"], "username": None, "credential": None}],
        "expires_at": None,
        "ttl_s": None,
    }


async def test_ephemeral_turn_credentials(
    client: httpx.AsyncClient, services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TURN_SECRET", SECRET)
    st = services.settings
    st.rtc_ice_servers = []
    st.turn_secret_ref = "secret:TURN_SECRET"
    st.turn_urls = ["turns:turn.example.com:5349"]
    st.turn_ttl_s = 900
    before = int(time.time())
    r = await client.get("/api/rtc/ice")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert SECRET not in r.text
    (turn,) = body["iceServers"]
    expiry = int(turn["username"].split(":")[0])
    assert before + 900 <= expiry <= int(time.time()) + 900 and body["expires_at"] == expiry
    assert body["ttl_s"] == 900 and turn["urls"] == ["turns:turn.example.com:5349"]
    expected = turn_credentials(SECRET, "auoreo", 900, now=expiry - 900)[1]
    assert turn["credential"] == expected


async def test_ice_requires_auth(services: Services) -> None:
    services.settings.server_password = "pw-123"
    app = create_app(services.settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        assert (await c.get("/api/rtc/ice")).status_code == 401
