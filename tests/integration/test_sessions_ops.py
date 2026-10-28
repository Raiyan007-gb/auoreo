"""Session fork/archive/delete/export, titles and slash commands (M3c)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.runtime.titles import clean_title


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


async def events_of(services: Services, aggregate: str) -> list[Any]:
    return [e for e in await services.events.replay(0) if e.aggregate_id == aggregate]


async def chat(services: Services, sid: str, text: str) -> None:
    await services.coordinator.submit(sid, text)
    await services.coordinator.wait(sid, 10)
    await services.coordinator.background_idle(10)


# ------------------------------------------------------------------ fork etc.
async def test_fork_copies_messages_and_links_both_sides(
    client: httpx.AsyncClient, services: Services, gateway: FakeGateway
) -> None:
    gateway.queue(Scripted(tool_calls=[("read", {"path": "nope.txt"})]), Scripted(text="done"))
    parent = await services.create_session(title="p", model="small")
    await chat(services, parent.id, "first")
    r = await client.post(f"/api/sessions/{parent.id}/fork")
    assert r.status_code == 201
    child = r.json()
    assert child["parent_id"] == parent.id and child["model"] == "small" and child["agent"] == "default"
    assert child["title"] == "p (fork)"
    pm = (await client.get(f"/api/sessions/{parent.id}/messages")).json()
    cm = (await client.get(f"/api/sessions/{child['id']}/messages")).json()
    assert cm == pm and len(cm) == 4
    forked = [e for e in await events_of(services, child["id"]) if e.type == "session.forked"]
    assert forked[0].data == {"parent_id": parent.id, "at_idx": 3}
    made = [e for e in await events_of(services, parent.id) if e.type == "session.fork_created"]
    assert made[0].data["child_id"] == child["id"]
    # the fork continues independently
    await chat(services, child["id"], "more")
    assert len((await client.get(f"/api/sessions/{parent.id}/messages")).json()) == 4
    assert (await client.post("/api/sessions/nope/fork")).status_code == 404


async def test_fork_and_delete_refuse_while_running(
    client: httpx.AsyncClient, services: Services, gateway: FakeGateway
) -> None:
    gateway.queue(Scripted(tool_calls=[("bash", {"command": "sleep 30"})]))
    sess = await services.create_session(title="t")
    await services.coordinator.submit(sess.id, "slow")
    for _ in range(200):
        if services.approvals.pending(sess.id):
            break
        await asyncio.sleep(0.02)
    assert (await client.post(f"/api/sessions/{sess.id}/fork")).status_code == 409
    assert (await client.delete(f"/api/sessions/{sess.id}")).status_code == 409
    await services.coordinator.cancel(sess.id)
    assert (await client.delete(f"/api/sessions/{sess.id}")).status_code == 200


async def test_archive_hides_from_list_unless_asked(client: httpx.AsyncClient, services: Services) -> None:
    a = await services.create_session(title="a")
    b = await services.create_session(title="b")
    r = await client.post(f"/api/sessions/{a.id}/archive")
    assert r.status_code == 200 and r.json()["archived"] is True
    ids = [s["id"] for s in (await client.get("/api/sessions")).json()]
    assert a.id not in ids and b.id in ids
    ids = [s["id"] for s in (await client.get("/api/sessions", params={"archived": "true"})).json()]
    assert a.id in ids and b.id in ids
    assert (await client.post(f"/api/sessions/{a.id}/unarchive")).json()["archived"] is False
    assert a.id in [s["id"] for s in (await client.get("/api/sessions")).json()]
    assert (await client.post("/api/sessions/nope/archive")).status_code == 404


async def test_delete_cascades_messages_but_keeps_events(
    client: httpx.AsyncClient, services: Services
) -> None:
    sess = await services.create_session(title="t")
    await chat(services, sess.id, "bye")
    before = await events_of(services, sess.id)
    r = await client.delete(f"/api/sessions/{sess.id}")
    assert r.json() == {"id": sess.id, "deleted": True}
    assert (await client.get(f"/api/sessions/{sess.id}")).status_code == 404
    assert await services.coordinator.runner.load_history(sess.id) == []
    assert len(await events_of(services, sess.id)) == len(before)  # append-only
    deleted = [e for e in await events_of(services, "server") if e.type == "session.deleted"]
    assert deleted[-1].data == {"session_id": sess.id}
    assert (await client.delete(f"/api/sessions/{sess.id}")).status_code == 404


async def test_export_json_and_markdown(
    client: httpx.AsyncClient, services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    (workdir / "a.txt").write_text("alpha")
    gateway.queue(Scripted(tool_calls=[("read", {"path": "a.txt"})]), Scripted(text="It says alpha."))
    sess = await services.create_session(title="Reading")
    await chat(services, sess.id, "what is in a.txt?")
    r = await client.get(f"/api/sessions/{sess.id}/export")
    assert r.headers["content-type"].startswith("application/json")
    data = json.loads(r.text)
    assert data["session"]["id"] == sess.id and len(data["messages"]) == 4
    md = (await client.get(f"/api/sessions/{sess.id}/export", params={"format": "md"})).text
    assert md.startswith("# Reading")
    assert "## User\n\nwhat is in a.txt?" in md
    assert '**tool** read({"path": "a.txt"}) → ' in md and "alpha" in md
    assert "It says alpha." in md
    assert (await client.get(f"/api/sessions/{sess.id}/export", params={"format": "pdf"})).status_code == 422


# --------------------------------------------------------------------- titles
async def test_title_generated_after_first_turn(services: Services, gateway: FakeGateway) -> None:
    gateway.queue(Scripted(text="hello there"), Scripted(text='"My Title."'))
    sess = await services.create_session()
    await chat(services, sess.id, "hi")
    row = await services.get_session(sess.id)
    assert row is not None and row.title == "My Title"
    title_req = gateway.requests[-1]
    assert title_req.tool_choice == "none" and title_req.model == "small"
    assert "at most 8 words" in title_req.messages[0]["content"]
    updated = [e for e in await events_of(services, sess.id) if e.type == "session.title_updated"]
    assert [e.data for e in updated] == [{"title": "My Title"}]
    # second turn: no new title
    await chat(services, sess.id, "again")
    updated = [e for e in await events_of(services, sess.id) if e.type == "session.title_updated"]
    assert len(updated) == 1


async def test_title_falls_back_to_user_text(services: Services, gateway: FakeGateway) -> None:
    long = "please summarise the quarterly numbers for the northern region and flag outliers"
    gateway.queue(Scripted(text="sure"), Scripted(error=GatewayError("RATE_LIMIT", "slow down")))
    sess = await services.create_session()
    await chat(services, sess.id, long)
    row = await services.get_session(sess.id)
    assert row is not None and row.title == long[:60]
    # the echoing fake gateway also falls back deterministically
    other = await services.create_session()
    await chat(services, other.id, "echo me")
    row = await services.get_session(other.id)
    assert row is not None and row.title == "echo me"


def test_clean_title() -> None:
    assert clean_title('  "Fix the build."  ') == "Fix the build"
    assert clean_title("Title: Hello\nextra") == "Hello"
    assert len(clean_title("x" * 200)) == 80


# ------------------------------------------------------------------- commands
async def test_commands_listed(client: httpx.AsyncClient) -> None:
    rows = {c["name"]: c for c in (await client.get("/api/commands")).json()}
    assert set(rows) >= {"compact", "model", "plan", "review"}
    assert rows["model"]["handler"] == "model" and rows["model"]["args_hint"]
    assert rows["review"]["handler"] == "prompt"


async def test_prompt_command_expands_body(services: Services, gateway: FakeGateway) -> None:
    sess = await services.create_session(title="t")
    await chat(services, sess.id, "/curator deploys please")  # G6: /review is now a subtask
    hist = await services.coordinator.runner.load_history(sess.id)
    assert hist[0]["content"].startswith(
        "Use `knowledge_search` to review what long-term memory holds about: deploys please"
    )


async def test_unknown_slash_word_is_left_alone(services: Services) -> None:
    sess = await services.create_session(title="t")
    await chat(services, sess.id, "/etc/hosts is a file")
    hist = await services.coordinator.runner.load_history(sess.id)
    assert hist[0]["content"] == "/etc/hosts is a file"


async def test_model_command_switches_without_a_turn(services: Services, gateway: FakeGateway) -> None:
    sess = await services.create_session(title="t")
    assert await services.coordinator.submit(sess.id, "/model small") is None
    row = await services.get_session(sess.id)
    assert row is not None and row.model == "small"
    changed = [e for e in await events_of(services, sess.id) if e.type == "session.model_changed"]
    assert changed[0].data["model"] == "small"
    assert gateway.requests == []
    await chat(services, sess.id, "hi")
    assert gateway.requests[-1].model == "small"


async def test_plan_command_uses_planner_for_one_turn(services: Services, gateway: FakeGateway) -> None:
    sess = await services.create_session(title="t")
    await chat(services, sess.id, "/plan add a cache")
    assert "You plan; you never change files." in gateway.requests[-1].messages[0]["content"]
    hist = await services.coordinator.runner.load_history(sess.id)
    assert hist[0]["content"] == "add a cache"
    started = [e for e in await events_of(services, sess.id) if e.type == "run.started"]
    assert started[-1].data["agent"] == "planner"
    await chat(services, sess.id, "normal")
    started = [e for e in await events_of(services, sess.id) if e.type == "run.started"]
    assert started[-1].data["agent"] == "default"


async def test_compact_without_service_is_unsupported(services: Services, gateway: FakeGateway) -> None:
    sess = await services.create_session(title="t")
    if hasattr(services.coordinator.runner, "compaction"):  # another workstream may add it
        services.coordinator.runner.compaction = None  # type: ignore[attr-defined]
    assert await services.coordinator.submit(sess.id, "/compact") is None
    failed = [e for e in await events_of(services, sess.id) if e.type == "run.failed"]
    assert failed[-1].data["code"] == "UNSUPPORTED"
    assert failed[-1].data["message"] == "compaction not available"
    assert gateway.requests == []


async def test_compact_calls_the_compaction_service(services: Services) -> None:
    calls: list[tuple[str, str]] = []

    class FakeCompaction:
        async def compact(self, session_id: str, *, trigger: str) -> None:
            calls.append((session_id, trigger))

    services.coordinator.runner.compaction = FakeCompaction()  # type: ignore[attr-defined]
    sess = await services.create_session(title="t")
    assert await services.coordinator.submit(sess.id, "/compact") is None
    assert calls == [(sess.id, "manual")]
