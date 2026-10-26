"""I8: `POST /api/livekit/token` (uuid identity, least grant, TTL ≤ 900 s, dispatch allowlist,
size caps, room ownership) and `POST /livekit/webhook` (JWT + sha256 of the RAW body with
compare_digest, fail-closed without secret, dedupe, rate limit). Real `livekit-api` minting;
the LiveKit server API is a fake."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core.services import Services
from tests.integration.test_rtc_join import use_livekit

pytest.importorskip("livekit.api")
SECRET = "s" * 40


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


def claims_of(token: str) -> dict[str, Any]:
    import jwt

    out: dict[str, Any] = jwt.decode(token, SECRET, algorithms=["HS256"])
    return out


async def test_token_is_least_grant_with_uuid_identity(
    client: httpx.AsyncClient, services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = use_livekit(services, monkeypatch, url="wss://sfu.example.net", token_ttl_s=600)
    row = await services.create_session(title="lk")
    r = await client.post(
        "/api/livekit/token",
        json={
            "session_id": row.id,
            "participant_identity": "alice@example.com",
            "participant_name": "Alice",
            "room_config": {"agents": [{"agent_name": "auoreo"}]},
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["server_url"] == "wss://sfu.example.net" and body["expires_in"] == 600
    assert str(uuid.UUID(body["participant_identity"])) == body["participant_identity"]
    claims = claims_of(body["participant_token"])
    assert claims["sub"] == body["participant_identity"] != "alice@example.com"
    assert claims["exp"] - claims["nbf"] <= 900 and claims["name"] == "Alice"
    assert claims["video"]["roomJoin"] and claims["video"]["room"] == body["room_name"]
    assert not any(claims["video"].get(k) for k in ("roomCreate", "roomList", "roomAdmin", "roomRecord"))
    assert claims["roomConfig"]["agents"][0]["agentName"] == "auoreo"
    assert calls == ["create_room"]
    # the same room again (owned by this session) → a fresh identity, no new room
    r2 = await client.post("/api/livekit/token", json={"session_id": row.id, "room_name": body["room_name"]})
    assert r2.status_code == 201 and r2.json()["participant_identity"] != body["participant_identity"]
    assert calls == ["create_room"]
    # audit row: hashes only, never the JWT
    from sqlalchemy import select

    from auoreo.core.db import AuditRow

    async with services.db.session() as s:
        rows = [
            r for r in (await s.execute(select(AuditRow))).scalars() if r.action == "livekit.token_minted"
        ]
    assert len(rows) == 2 and "eyJ" not in json.dumps(rows[0].detail) and rows[0].detail["outcome"] == "ok"


async def test_token_refusals(
    client: httpx.AsyncClient, services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = await client.post("/api/livekit/token", json={})
    assert r.status_code == 501  # aiortc is the transport
    use_livekit(services, monkeypatch, url="wss://sfu.example.net")
    assert (await client.post("/api/livekit/token", json={"room_name": "not-ours"})).status_code == 403
    r = await client.post("/api/livekit/token", json={"room_config": {"agents": [{"agent_name": "rogue"}]}})
    assert r.status_code == 403
    r = await client.post("/api/livekit/token", json={"room_config": {"egress": {"x": 1}}})
    assert r.status_code == 422  # only agent dispatch is accepted
    r = await client.post("/api/livekit/token", json={"participant_metadata": "x" * 2000})
    assert r.status_code == 400
    r = await client.post("/api/livekit/token", json={"session_id": "ses_missing"})
    assert r.status_code == 404
    services.settings.livekit = services.settings.livekit.model_copy(update={"url": "wss://x.livekit.cloud"})
    assert (await client.post("/api/livekit/token", json={})).status_code == 403  # Cloud without consent


def signed(body: bytes, *, secret: str = SECRET, sha: bytes | None = None) -> str:
    from livekit import api

    digest = base64.b64encode(sha if sha is not None else hashlib.sha256(body).digest()).decode()
    return str(api.AccessToken("APIkey123", secret).with_sha256(digest).to_jwt())


def event_body(event: str = "room_started", event_id: str = "EV_1", room: str = "r1") -> bytes:
    return json.dumps({"event": event, "id": event_id, "room": {"name": room}, "createdAt": "1"}).encode()


async def test_webhook_verifies_raw_body(
    client: httpx.AsyncClient, services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = event_body()
    r = await client.post("/livekit/webhook", content=body, headers={"Authorization": signed(body)})
    assert r.status_code == 403  # no API key/secret configured: fail closed
    use_livekit(services, monkeypatch, url="wss://sfu.example.net")
    headers = {"Authorization": signed(body), "Content-Type": "application/webhook+json"}
    r = await client.post("/livekit/webhook", content=body, headers=headers)
    assert r.status_code == 200 and r.json() == {
        "status": "ok",
        "event": "room_started",
        "id": "EV_1",
        "extra": {},
    }
    dup = await client.post("/livekit/webhook", content=body, headers=headers)
    assert dup.status_code == 200 and dup.json()["status"] == "duplicate"
    evs = [e for e in await services.events.replay(0, None) if e.type == "livekit.webhook"]
    assert len(evs) == 1 and evs[0].data["event"] == "room_started" and evs[0].data["room_hash"]
    # re-serialised JSON (same content, different bytes) fails the hash check
    reser = json.dumps(json.loads(body), indent=2).encode()
    assert (await client.post("/livekit/webhook", content=reser, headers=headers)).status_code == 401
    bad_secret = {"Authorization": signed(body, secret="t" * 40)}
    assert (await client.post("/livekit/webhook", content=body, headers=bad_secret)).status_code == 401
    assert (await client.post("/livekit/webhook", content=body)).status_code == 401
    assert (
        await client.post(
            "/livekit/webhook", content=body, headers={"Authorization": "Bearer " + signed(body)}
        )
    ).status_code == 200


async def test_webhook_rate_limit_after_auth(
    client: httpx.AsyncClient, services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_livekit(services, monkeypatch, url="wss://sfu.example.net")
    services.settings.i8.livekit.webhook_rate_per_min = 2
    codes = []
    for i in range(3):
        body = event_body(event_id=f"EV_R{i}")
        r = await client.post("/livekit/webhook", content=body, headers={"Authorization": signed(body)})
        codes.append(r.status_code)
    assert codes == [200, 200, 429]
    services.settings.i8.livekit.webhook_enabled = False
    body = event_body(event_id="EV_off")
    assert (
        await client.post("/livekit/webhook", content=body, headers={"Authorization": signed(body)})
    ).status_code == 404


async def test_capabilities_report_the_livekit_toggle(
    client: httpx.AsyncClient, services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    caps = (await client.get("/api/rtc/capabilities")).json()
    assert caps["livekit"] is False and caps["livekit_agent"] is None
    use_livekit(services, monkeypatch, url="wss://sfu.example.net")
    services.settings.i8.livekit.worker.enabled = True
    caps = (await client.get("/api/rtc/capabilities")).json()
    assert caps["livekit"] is True and caps["livekit_agent"] == "auoreo"
