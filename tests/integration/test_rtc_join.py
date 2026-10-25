"""`POST /api/rtc/join` + `DELETE /api/rtc/join/{id}`: transport-agnostic join through the selected
`rtc_transport` backend (aiortc for real; LiveKit with a faked server API). The M4e routes are
unchanged and share the aiortc peers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core.services import Services


@pytest.fixture
async def client(services: Services) -> AsyncIterator[httpx.AsyncClient]:
    services.settings.rtc_ice_servers = []
    app = create_app(services.settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=20) as c,
    ):
        yield c


async def client_offer() -> tuple[Any, str]:
    from aiortc import RTCPeerConnection

    pc = RTCPeerConnection()
    pc.createDataChannel("auoreo-events")
    await pc.setLocalDescription(await pc.createOffer())
    return pc, pc.localDescription.sdp


async def test_aiortc_join_shares_peers_with_the_m4e_routes(client: httpx.AsyncClient) -> None:
    pytest.importorskip("aiortc")
    pc, sdp = await client_offer()
    try:
        r = await client.post("/api/rtc/join", json={"sdp": sdp, "identity": "tablet"})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["transport"] == "aiortc" and body["sdp"].startswith("v=0") and body["token"] is None
        join_id = body["join_id"]
        assert join_id.startswith("rtc_") and body["extra"]["rtc_id"] == join_id
        assert [p["rtc_id"] for p in (await client.get("/api/rtc")).json()] == [join_id]
        r = await client.delete(f"/api/rtc/join/{join_id}")
        assert r.json() == {"transport": "aiortc", "join_id": join_id}
        assert (await client.get("/api/rtc")).json() == []
    finally:
        await pc.close()


async def test_join_validation(client: httpx.AsyncClient) -> None:
    pytest.importorskip("aiortc")
    r = await client.post("/api/rtc/join", json={})
    assert r.status_code == 400 and "SDP offer" in r.json()["detail"]
    r = await client.post("/api/rtc/join", json={"session_id": "nope", "sdp": "v=0"})
    assert r.status_code == 404
    r = await client.post("/api/rtc/join", json={"identity": "bad identity!"})
    assert r.status_code == 422


async def test_m4e_routes_unchanged(client: httpx.AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert {
        "/api/rtc/offer",
        "/api/rtc/{rtc_id}",
        "/api/rtc",
        "/api/rtc/join",
        "/api/rtc/join/{join_id}",
    } <= set(paths)


def use_livekit(services: Services, monkeypatch: pytest.MonkeyPatch, **cfg: Any) -> list[str]:
    monkeypatch.setenv("LIVEKIT_API_KEY", "APIkey123")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "s" * 40)
    services.settings.backends["rtc_transport"] = "livekit"
    services.settings.livekit = services.settings.livekit.model_copy(update=cfg)
    calls: list[str] = []

    class Svc:
        def __getattr__(self, name: str) -> Any:
            async def call(req: Any) -> Any:
                calls.append(name)
                return SimpleNamespace()

            return call

    async def aclose() -> None:
        return None

    bdef = services.harness.select_backend("rtc_transport", "livekit")
    assert bdef is not None
    api = SimpleNamespace(room=Svc(), aclose=aclose)
    services._backends["rtc_transport"] = bdef.load()(services.settings, api_factory=lambda: api)
    return calls


async def test_livekit_join_returns_room_token(
    client: httpx.AsyncClient, services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("livekit.api")
    import jwt

    calls = use_livekit(services, monkeypatch, url="wss://sfu.example.net")
    row = await services.create_session(title="teleop")
    r = await client.post("/api/rtc/join", json={"session_id": row.id, "identity": "operator"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["transport"] == "livekit" and body["url"] == "wss://sfu.example.net" and body["sdp"] is None
    assert body["room"] == body["join_id"] == f"auoreo-{row.id}"
    claims = jwt.decode(body["token"], "s" * 40, algorithms=["HS256"])
    assert claims["sub"] == "operator" and claims["video"] == {
        "roomJoin": True,
        "room": body["room"],
        "canPublish": True,
        "canSubscribe": True,
        "canPublishData": True,
    }  # no admin/create grants
    assert calls == ["create_room"]
    r = await client.delete(f"/api/rtc/join/{body['join_id']}")
    assert r.status_code == 200 and calls == ["create_room", "delete_room"]


async def test_livekit_errors_map_to_status_codes(
    client: httpx.AsyncClient, services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("livekit.api")
    use_livekit(services, monkeypatch, url="wss://proj.livekit.cloud")
    r = await client.post("/api/rtc/join", json={})
    assert r.status_code == 403 and "cloud_consent" in r.json()["detail"]
    use_livekit(services, monkeypatch, url=None)
    r = await client.post("/api/rtc/join", json={})
    assert r.status_code == 501 and "settings.livekit.url" in r.json()["detail"]


def test_settings_append_livekit_defaults(home: Path) -> None:
    from auoreo.core.settings import Settings

    lk = Settings(home=home).livekit
    assert lk.url is None and lk.cloud_consent is False and lk.token_ttl_s == 3600
    assert lk.sip.krisp_enabled and lk.sip.room_prefix == "auoreo-call-"
