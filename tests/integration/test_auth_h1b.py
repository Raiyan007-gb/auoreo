"""H1b: the browser cookie is a short-lived session JWT (never the password), refreshed on
activity and revoked on logout; `/ws` honours `--ws-auth token|jwt|capability` (spec §18,
§24.7, §25.14)."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

import httpx
import jwt
import pytest
from asgi_lifespan import LifespanManager
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from auoreo.api.app import create_app
from auoreo.core.services import Services
from auoreo.core.session_jwt import KEY_SECRET, SessionTokens, tokens_for
from auoreo.core.settings import Settings
from tests.integration.test_ws import hello, recv_until, running_server

PW = "s3cret-pass"
SAME = {"Origin": "http://127.0.0.1"}


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    settings.server_password = PW
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


async def _login(c: httpx.AsyncClient) -> str:
    r = await c.post("/api/auth/login", json={"password": PW})
    assert r.status_code == 200 and r.json()["method"] == "jwt"
    token = c.cookies["auoreo_token"]
    assert token != PW and PW not in r.headers["set-cookie"]
    return token


async def test_login_mints_short_lived_jwt_with_jti(client: httpx.AsyncClient, services: Services) -> None:
    token = await _login(client)
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["exp"] - claims["iat"] <= 900 and claims["jti"] and claims["sid"]
    assert claims["typ"] == "session" and claims["ip"] == "127.0.0.1"
    assert (
        "max-age=900"
        in (await client.post("/api/auth/login", json={"password": PW})).headers["set-cookie"].lower()
    )
    # the signing key is a secret in the store (env / keyring / secrets.db), not the password
    key = services.secrets.get(KEY_SECRET)
    assert key and key != PW
    jwt.decode(token, key, algorithms=["HS256"], issuer="auoreo")
    status = (await client.get("/api/auth/status")).json()
    assert status["authenticated"] and status["method"] == "jwt" and status["expires_at"] == claims["exp"]


async def test_cookie_jwt_authenticates_rest_and_sse_and_bearer_password_still_works(
    client: httpx.AsyncClient,
) -> None:
    assert (await client.get("/api/sessions")).status_code == 401
    await _login(client)
    assert (await client.get("/api/sessions")).status_code == 200
    assert (await client.get("/api/events", params={"follow": "false"})).status_code == 200
    assert (await client.post("/api/sessions", json={})).status_code == 403  # CSRF still enforced
    assert (await client.post("/api/sessions", json={}, headers=SAME)).status_code == 201
    client.cookies.clear()
    bearer = {"Authorization": f"Bearer {PW}"}
    assert (await client.post("/api/sessions", json={}, headers=bearer)).status_code == 201


async def test_forged_expired_or_foreign_ip_tokens_are_refused(
    client: httpx.AsyncClient, services: Services
) -> None:
    token = await _login(client)
    claims = jwt.decode(token, options={"verify_signature": False})
    forged = jwt.encode({**claims, "exp": int(time.time()) + 60}, "not-the-key", algorithm="HS256")
    client.cookies.set("auoreo_token", forged)
    assert (await client.get("/api/sessions")).status_code == 401
    key = services.secrets.get(KEY_SECRET)
    assert key
    expired = jwt.encode({**claims, "iat": 1, "exp": 2}, key, algorithm="HS256")
    client.cookies.set("auoreo_token", expired)
    assert (await client.get("/api/sessions")).status_code == 401
    elsewhere = jwt.encode({**claims, "ip": "10.9.9.9"}, key, algorithm="HS256")
    client.cookies.set("auoreo_token", elsewhere)
    assert (await client.get("/api/sessions")).status_code == 401
    client.cookies.set("auoreo_token", PW)  # the raw password is never a valid cookie
    assert (await client.get("/api/sessions")).status_code == 401


async def test_activity_refreshes_the_cookie(client: httpx.AsyncClient, settings: Settings) -> None:
    settings.h1b.jwt_refresh_after_s = 0
    first = await _login(client)
    await asyncio.sleep(1.1)  # iat has 1 s resolution
    r = await client.get("/api/sessions")
    assert r.status_code == 200 and "auoreo_token=" in r.headers.get("set-cookie", "")
    second = client.cookies["auoreo_token"]
    a, b = (jwt.decode(t, options={"verify_signature": False}) for t in (first, second))
    assert b["sid"] == a["sid"] and b["jti"] != a["jti"] and b["exp"] > a["exp"]
    settings.h1b.jwt_refresh_after_s = 10_000
    assert "set-cookie" not in (await client.get("/api/sessions")).headers


async def test_logout_revokes_every_token_of_the_login(
    client: httpx.AsyncClient, services: Services, settings: Settings
) -> None:
    old = await _login(client)
    assert (await client.post("/api/auth/logout", headers=SAME)).status_code == 200
    client.cookies.set("auoreo_token", old)
    assert (await client.get("/api/sessions")).status_code == 401
    # persisted: a fresh minter (restart) still refuses it
    fresh = SessionTokens(settings, services.secrets)
    assert fresh.verify(old, ip="127.0.0.1") is None and len(fresh.revoked) >= 1


async def test_capability_endpoint_needs_auth_and_a_real_session(
    client: httpx.AsyncClient, services: Services
) -> None:
    s = await services.create_session(title="cap")
    assert (await client.post("/api/auth/capability", json={"session_id": s.id})).status_code == 401
    bearer = {"Authorization": f"Bearer {PW}"}
    r = await client.post("/api/auth/capability", json={"session_id": "nope"}, headers=bearer)
    assert r.status_code == 404
    r = await client.post("/api/auth/capability", json={"session_id": s.id}, headers=bearer)
    assert r.status_code == 200 and r.json()["session_id"] == s.id
    cap = r.json()["token"]
    # a capability is not a REST credential
    assert (await client.get("/api/sessions", headers={"Authorization": f"Bearer {cap}"})).status_code == 401


# ------------------------------------------------------------------ /ws modes
async def _refused(url: str, **kw: object) -> None:
    with pytest.raises(InvalidStatus):
        async with connect(url, **kw):  # type: ignore[arg-type]
            pass


async def test_ws_jwt_mode_refuses_the_raw_password(services: Services, settings: Settings) -> None:
    settings.server_password = PW
    settings.cli.ws_auth = "jwt"
    token, _ = tokens_for(services).mint(ip="127.0.0.1")
    async with running_server(services, settings) as url:
        await _refused(f"{url}?token={PW}")
        await _refused(url, additional_headers={"Authorization": f"Bearer {PW}"})
        async with connect(f"{url}?token={token}") as ws:
            assert (await hello(ws))["server_seq"] >= 0
        async with connect(url, additional_headers={"Cookie": f"auoreo_token={token}"}) as ws:
            await hello(ws)


async def test_ws_token_mode_accepts_password_and_jwt_cookie(services: Services, settings: Settings) -> None:
    settings.server_password = PW
    token, _ = tokens_for(services).mint(ip="127.0.0.1")
    async with running_server(services, settings) as url:
        async with connect(f"{url}?token={PW}") as ws:
            await hello(ws)
        async with connect(url, additional_headers={"Cookie": f"auoreo_token={token}"}) as ws:
            await hello(ws)
        await _refused(url, additional_headers={"Cookie": f"auoreo_token={PW}"})


async def test_ws_capability_mode_scopes_the_connection(services: Services, settings: Settings) -> None:
    settings.server_password = PW
    settings.cli.ws_auth = "capability"
    mine = await services.create_session(title="mine")
    other = await services.create_session(title="other")
    cap, _ = tokens_for(services).mint(ip="127.0.0.1", kind="cap", session_id=mine.id)
    session_jwt, _ = tokens_for(services).mint(ip="127.0.0.1")
    async with running_server(services, settings) as url:
        await _refused(f"{url}?token={PW}")
        await _refused(f"{url}?token={session_jwt}")
        await _refused(url, additional_headers={"Cookie": f"auoreo_token={session_jwt}"})
        async with connect(f"{url}?token={cap}") as ws:
            await hello(ws)  # asks for everything; gets only its own session
            await services.emit(other.id, "custom.other", {"n": 1})
            await services.emit(mine.id, "custom.mine", {"n": 2})
            frames = await recv_until(ws, lambda f: f.get("type") == "custom.mine")
            assert all(f.get("aggregate_id") != other.id for f in frames)
            msg = {"kind": "interrupt", "session_id": other.id}
            await ws.send(json.dumps({"type": "control_request", "request_id": "x1", "request": msg}))
            acks = await recv_until(ws, lambda f: f.get("type") == "ack")
            assert acks[-1]["ok"] is False and "scoped" in acks[-1]["detail"]


async def test_ws_closes_when_the_login_is_revoked(services: Services, settings: Settings) -> None:
    settings.server_password = PW
    settings.cli.ws_auth = "jwt"
    settings.ws_ping_interval_s = 0.1
    token, claims = tokens_for(services).mint(ip="127.0.0.1")
    async with running_server(services, settings) as url, connect(f"{url}?token={token}") as ws:
        await hello(ws)
        tokens_for(services).revoke(claims)
        with pytest.raises(Exception):  # noqa: B017 - ConnectionClosed with 4401
            for _ in range(50):
                await asyncio.wait_for(ws.recv(), 2)
        assert ws.close_code == 4401


def test_serve_accepts_jwt_and_capability_with_a_password(home: object) -> None:
    from pathlib import Path

    from auoreo.cli.serve_extras import resolve

    for mode in ("jwt", "capability"):
        st = Settings(home=Path(str(home)), server_password=PW)
        resolve(st, ws_auth=mode)
        assert st.cli.ws_auth == mode
