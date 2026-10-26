"""G1a HTTP control plane: idempotent creates, Host check, cookie login + CSRF, cookie-gated
token injection, `?channels=`, the replay cap (206) and `POST /api/events:tail`."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import func, select

from auoreo.api.app import create_app
from auoreo.api.security import host_of
from auoreo.core.db import IdempotencyRow, SessionRow
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from tests.integration.test_api import parse_sse

PW = "s3cret-pass"


def client_for(app: object, host: str = "127.0.0.1") -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{host}")  # type: ignore[arg-type]


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with LifespanManager(app), client_for(app) as c:
        yield c


async def session_count(svc: Services) -> int:
    async with svc.db.session() as s:
        return int((await s.execute(select(func.count()).select_from(SessionRow))).scalar_one())


# ------------------------------------------------------------------ idempotency
async def test_duplicate_create_replays_without_rerunning(
    client: httpx.AsyncClient, services: Services
) -> None:
    key = {"Idempotency-Key": str(uuid.uuid4())}
    r1 = await client.post("/api/sessions", json={"title": "once"}, headers=key)
    r2 = await client.post("/api/sessions", json={"title": "once"}, headers=key)
    assert r1.status_code == 201 and r2.status_code == 200
    assert r2.headers["idempotent-replayed"] == "true"
    assert r2.json() == {**r1.json(), "deduped": True}
    assert await session_count(services) == 1
    # the same key on another route is a different claim
    goal = await client.post("/api/goals", json={"title": "g", "objective": "o"}, headers=key)
    assert goal.status_code in (201, 422)
    assert (await client.post("/api/sessions", json={}, headers={"Idempotency-Key": "x"})).status_code == 400


async def test_concurrent_duplicates_execute_once(client: httpx.AsyncClient, services: Services) -> None:
    key = {"Idempotency-Key": str(uuid.uuid4())}
    results = await asyncio.gather(
        *(client.post("/api/sessions", json={"title": "race"}, headers=key) for _ in range(8))
    )
    codes = sorted(r.status_code for r in results)
    assert codes.count(201) == 1 and set(codes) <= {200, 201, 409}, codes
    assert await session_count(services) == 1
    created = next(r for r in results if r.status_code == 201).json()
    assert all(r.json()["id"] == created["id"] for r in results if r.status_code == 200)


async def test_failed_attempt_releases_the_key(client: httpx.AsyncClient, services: Services) -> None:
    key = {"Idempotency-Key": str(uuid.uuid4())}
    bad = await client.post("/api/sessions/nope/fork", headers=key)
    assert bad.status_code == 404
    async with services.db.session() as s:
        assert (await s.execute(select(func.count()).select_from(IdempotencyRow))).scalar_one() == 0
    sess = (await client.post("/api/sessions", json={})).json()
    ok = await client.post(f"/api/sessions/{sess['id']}/fork", headers=key)
    assert ok.status_code == 201


async def test_admit_is_claim_at_most_once(services: Services) -> None:
    idem, key = services.idempotency, str(uuid.uuid4())
    states = [a.state for a in await asyncio.gather(*(idem.admit(key, "POST /x") for _ in range(10)))]
    assert states.count("fresh") == 1 and states.count("in_progress") == 9
    await idem.complete(key, "POST /x", 201, {"id": 7})
    dup = await idem.admit(key, "POST /x")
    assert dup.state == "duplicate" and dup.status_code == 201 and dup.body == {"id": 7}


async def test_chat_body_request_id_still_dedupes(client: httpx.AsyncClient, services: Services) -> None:
    rid = str(uuid.uuid4())
    r1 = await client.post("/api/chat", json={"message": "hi", "request_id": rid})
    r2 = await client.post("/api/chat", json={"message": "hi", "request_id": rid})
    assert r1.status_code == 202 and r2.status_code == 200 and r2.json()["deduped"] is True
    assert r2.json()["session_id"] == r1.json()["session_id"]
    await services.coordinator.wait(r1.json()["session_id"], 10)


# ------------------------------------------------------------------ Host header
def test_host_of_parses_ports_and_ipv6() -> None:
    assert host_of("127.0.0.1:4096") == "127.0.0.1"
    assert host_of("[::1]:4096") == "::1" and host_of("[::1]") == "::1"
    assert host_of("Box.LAN.") == "box.lan"


async def test_wrong_host_is_400(services: Services, settings: Settings) -> None:
    settings.http.trusted_hosts = ["box.lan", "*.example.com"]
    app = create_app(settings, services=services)
    async with LifespanManager(app):
        for host, code in [
            ("evil.com", 400),
            ("127.0.0.1:4096", 200),
            ("localhost", 200),
            ("[::1]:9", 200),
            ("box.lan:4096", 200),
            ("a.example.com", 200),
            ("example.com.evil.net", 400),
        ]:
            async with client_for(app) as c:
                r = await c.get("/api/health", headers={"Host": host})
            assert r.status_code == code, (host, r.status_code)
        async with client_for(app) as c:  # webhooks are not exempt
            assert (await c.post("/hooks/x", headers={"Host": "evil.com"})).status_code == 400


async def test_tunnel_host_is_trusted(services: Services, settings: Settings) -> None:
    (settings.home / "tunnel.json").write_text('{"url": "https://abc.trycloudflare.com", "provider": "x"}')
    app = create_app(settings, services=services)
    async with LifespanManager(app), client_for(app) as c:
        assert (await c.get("/api/health", headers={"Host": "abc.trycloudflare.com"})).status_code == 200


# ------------------------------------------------------------------ cookie login, CSRF, token meta
@pytest.fixture
def dist(tmp_path: Path) -> Path:
    d = tmp_path / "dist"
    d.mkdir()
    (d / "index.html").write_text("<html><head><title>x</title></head><body></body></html>")
    return d


async def test_login_cookie_csrf_and_meta_injection(
    services: Services, settings: Settings, dist: Path
) -> None:
    settings.server_password = PW
    app = create_app(settings, services=services, web_dist=dist)
    async with LifespanManager(app), client_for(app) as c:
        anon = await c.get("/")
        assert anon.status_code == 200 and "auoreo-token" not in anon.text  # never to the unauthenticated
        assert "auoreo-token" not in (await c.get("/session/abc")).text
        assert (await c.post("/api/auth/login", json={"password": "nope"})).status_code == 401
        r = await c.post("/api/auth/login", json={"password": PW})
        assert r.status_code == 200 and r.json()["secured"] and r.json()["authenticated"]
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie and "secure" not in cookie
        assert PW.lower() not in cookie  # H1b: the cookie is a session JWT, never the password
        page = await c.get("/")
        assert '<meta name="auoreo-auth" content="cookie">' in page.text and PW not in page.text
        assert page.headers["cache-control"] == "no-store"
        assert (await c.get("/session/abc")).text.count("auoreo-auth") == 1  # SPA deep link too
        # cookie auth works for GET (SSE) …
        assert (await c.get("/api/events", params={"follow": "false"})).status_code == 200
        # … but an unsafe method needs a same-origin Origin/Referer (CSRF)
        assert (await c.post("/api/sessions", json={})).status_code == 403
        cross = {"Origin": "http://evil.example"}
        assert (await c.post("/api/sessions", json={}, headers=cross)).status_code == 403
        same = {"Origin": "http://127.0.0.1"}
        assert (await c.post("/api/sessions", json={}, headers=same)).status_code == 201
        referer = {"Referer": "http://127.0.0.1/sessions"}
        assert (await c.post("/api/sessions", json={}, headers=referer)).status_code == 201
        assert (await c.get("/api/auth/status")).json()["authenticated"] is True
        await c.post("/api/auth/logout", headers=same)
        c.cookies.clear()
        status = (await c.get("/api/auth/status")).json()
        assert status["secured"] is True and status["authenticated"] is False
        async with client_for(app) as c2:  # bearer needs no Origin
            await _bearer_checks(c2)


async def _bearer_checks(c: httpx.AsyncClient) -> None:
    bearer = {"Authorization": f"Bearer {PW}"}
    assert (await c.post("/api/sessions", json={}, headers=bearer)).status_code == 201
    assert "auoreo-auth" not in (await c.get("/", headers=bearer)).text  # cookie only


async def test_login_cookie_is_secure_behind_https(services: Services, settings: Settings) -> None:
    settings.server_password = PW
    app = create_app(settings, services=services)
    async with LifespanManager(app), client_for(app) as c:
        r = await c.post("/api/auth/login", json={"password": PW}, headers={"X-Forwarded-Proto": "https"})
        assert "secure" in r.headers["set-cookie"].lower()


# ------------------------------------------------------------------ events
async def test_channels_filter_and_job_channel(client: httpx.AsyncClient, services: Services) -> None:
    a = await services.create_session(title="a")
    await services.emit(a.id, "custom.a", {"x": 1})
    await services.emit("server", "cron.fired", {"job": "nightly"})
    await services.emit("server", "cron.fired", {"job": "other"})
    r = await client.get("/api/events", params={"follow": "false", "channels": "job:nightly"})
    assert [e["data"].get("job") for e in parse_sse(r.text)] == ["nightly"]
    r = await client.get("/api/events", params={"follow": "false", "channels": f"session:{a.id},job:other"})
    types = {(e["type"], e["data"].get("job")) for e in parse_sse(r.text)}
    assert (
        ("custom.a", None) in types
        and ("cron.fired", "other") in types
        and ("cron.fired", "nightly") not in types
    )
    assert (await client.get("/api/events", params={"channels": "bogus"})).status_code == 400


async def test_replay_cap_returns_206_and_cap_warning(client: httpx.AsyncClient, services: Services) -> None:
    services.settings.http.replay_cap = 3
    for i in range(5):
        await services.emit("server", "custom.n", {"i": i})
    r = await client.get("/api/events", params={"follow": "false"})
    assert r.status_code == 206
    assert "event: cap_warning" in r.text
    events = parse_sse(r.text)
    assert len([e for e in events if e.get("type") == "custom.n"]) == 3
    warn = events[-1]
    assert warn["replayed"] == 3 and warn["cap"] == 3
    rest = await client.get(
        "/api/events", params={"follow": "false"}, headers={"Last-Event-ID": str(warn["cursor"])}
    )
    assert rest.status_code == 200 and [e["data"]["i"] for e in parse_sse(rest.text)] == [3, 4]


async def test_events_tail(client: httpx.AsyncClient, services: Services) -> None:
    for i in range(5):
        await services.emit("server", "custom.t", {"i": i})
    js = {"Accept": "application/json"}
    r = await client.post("/api/events:tail", json={"limit": 2}, headers=js)
    body = r.json()
    assert r.status_code == 200 and body["seq"] == await services.events.last_seq()
    assert [e["data"]["i"] for e in body["events"]] == [3, 4]
    first = body["events"][0]["seq"]
    after = (await client.post("/api/events:tail", json={"after_seq": first, "limit": 10}, headers=js)).json()
    assert [e["data"]["i"] for e in after["events"]] == [4]
    assert (await client.post("/api/events:tail", headers=js)).status_code == 200
