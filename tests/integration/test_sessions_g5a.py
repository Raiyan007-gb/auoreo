"""G5a session endpoints: rename, share links (+ public view), retry, revert; health code_sha."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core import sessions_more as more
from auoreo.core.buildinfo import code_sha
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway, Scripted


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


async def chat(services: Services, sid: str, text: str) -> None:
    await services.coordinator.submit(sid, text)
    await services.coordinator.wait(sid, 10)
    await services.coordinator.background_idle(10)


async def types_of(services: Services, sid: str) -> list[str]:
    return [e.type for e in await services.events.replay(0) if e.aggregate_id == sid]


async def test_health_reports_code_sha(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/health")).json()
    assert body["code_sha"] == code_sha() and len(body["code_sha"]) >= 12


async def test_session_created_event_carries_cwd(services: Services) -> None:
    row = await services.create_session()
    created = [e for e in await services.events.replay(0) if e.type == "session.created"][-1]
    assert created.aggregate_id == row.id and created.data["cwd"] == str(services.cwd)


async def test_rename(client: httpx.AsyncClient, services: Services) -> None:
    row = await services.create_session(title="old")
    r = await client.patch(f"/api/sessions/{row.id}", json={"title": "  new   name "})
    assert r.status_code == 200 and r.json()["title"] == "new name"
    assert "session.renamed" in await types_of(services, row.id)
    assert (await client.patch("/api/sessions/ses_nope", json={"title": "x"})).status_code == 404
    assert (await client.patch(f"/api/sessions/{row.id}", json={"title": ""})).status_code == 422
    assert (await client.patch(f"/api/sessions/{row.id}", json={"title": "   "})).status_code == 400


async def test_share_link_lifecycle(
    client: httpx.AsyncClient, services: Services, settings: Settings, gateway: FakeGateway
) -> None:
    gateway.queue(Scripted(text="the key is sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcd"))
    row = await services.create_session(title="shared")
    await chat(services, row.id, "hello")
    made = await client.post(f"/api/sessions/{row.id}/share")
    assert made.status_code == 201
    share = made.json()
    assert share["token"].startswith("shr_") and share["url"].endswith(share["path"])
    events = [e for e in await services.events.replay(0) if e.type == "session.shared"]
    assert share["token"] not in str(events[0].data)  # only the digest is stored
    settings.server_password = "pw"  # the share view stays public; the API needs the password
    try:
        assert (await client.get(f"/api/sessions/{row.id}")).status_code == 401
        view = await client.get(share["path"])
        assert view.status_code == 200
        body = view.json()
        assert body["session"]["id"] == row.id and [m["role"] for m in body["messages"]] == [
            "user",
            "assistant",
        ]
        assert "ABCDEFGHIJKLMNOP" not in str(body["messages"])  # forced redaction
        auth = {"Authorization": "Bearer pw"}
        revoked = await client.delete(f"/api/sessions/{row.id}/share", headers=auth)
        assert revoked.json() == {"session_id": row.id, "revoked": 1}
    finally:
        settings.server_password = None
    assert (await client.get(share["path"])).status_code == 404
    assert (await client.get("/api/shared/shr_madeup")).status_code == 404
    assert (await client.get("/api/shared/not-a-token")).status_code == 404


async def test_share_unknown_session(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/sessions/ses_nope/share")).status_code == 404


async def test_revert_truncates_and_records(client: httpx.AsyncClient, services: Services) -> None:
    row = await services.create_session()
    await chat(services, row.id, "one")
    await chat(services, row.id, "two")
    assert len((await client.get(f"/api/sessions/{row.id}/messages")).json()) == 4
    r = await client.post(f"/api/sessions/{row.id}/revert", json={"to_idx": 1})
    assert r.status_code == 200 and r.json()["removed"] == 2
    msgs = (await client.get(f"/api/sessions/{row.id}/messages")).json()
    assert [m["content"] for m in msgs][0] == "one" and len(msgs) == 2
    reverted = [e for e in await services.events.replay(0) if e.type == "session.reverted"][0]
    assert reverted.data["removed"] == 2 and reverted.data["removed_messages"][0]["content"] == "two"
    again = await client.post(f"/api/sessions/{row.id}/revert", json={"to_idx": 5})
    assert again.json()["removed"] == 0
    assert (await client.post(f"/api/sessions/{row.id}/revert", json={"to_idx": -2})).status_code == 422
    # the next turn sees the shortened history (runtime cache dropped)
    await chat(services, row.id, "three")
    msgs = (await client.get(f"/api/sessions/{row.id}/messages")).json()
    assert [m["content"] for m in msgs if m["role"] == "user"] == ["one", "three"]


async def test_retry_reruns_last_user_turn(
    client: httpx.AsyncClient, services: Services, gateway: FakeGateway
) -> None:
    gateway.queue(Scripted(text="first answer"))
    row = await services.create_session(title="t")  # titled: no aux title call takes a script
    await chat(services, row.id, "question")
    r = await client.post(f"/api/sessions/{row.id}/retry")
    assert r.status_code == 202 and r.json()["text"] == "question" and r.json()["run_id"]
    await services.coordinator.wait(row.id, 10)
    await services.coordinator.background_idle(10)
    msgs = (await client.get(f"/api/sessions/{row.id}/messages")).json()
    assert [m["content"] for m in msgs] == ["question", "echo: question"]  # old answer cut
    assert {"session.reverted", "session.retried"} <= set(await types_of(services, row.id))


async def test_retry_without_user_message(client: httpx.AsyncClient, services: Services) -> None:
    row = await services.create_session()
    assert (await client.post(f"/api/sessions/{row.id}/retry")).status_code == 400


async def test_last_user_turn_skips_image_followups() -> None:
    history = [
        {"role": "user", "content": "real"},
        {"role": "assistant", "content": None, "tool_calls": []},
        {"role": "user", "content": [{"type": "text", "text": "img"}]},
    ]
    assert more.last_user_turn(history) == (0, "real")
    assert more.last_user_turn([]) is None
