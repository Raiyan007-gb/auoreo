"""G6 user preference learning: the agent proposes PREFS.md changes, a human approves (the
approval event id lands in the entry) or denies; the memory tool never writes PREFS.md."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.memory.prefs import PrefsError, PrefsInbox, ProposalNotFound
from auoreo.tools.context import ToolContext
from tests.unit.test_tools_m2 import ctx_for


def prefs_text(home: Path) -> str:
    path = home / "memory" / "PREFS.md"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


# ------------------------------------------------------------------- inbox
def test_propose_approve_deny_cycle(home: Path) -> None:
    box = PrefsInbox(home)
    p = box.propose("add", "Answer in British English", reason="said so twice", session_id="ses_1")
    assert p.status == "pending" and "British" not in prefs_text(home)
    assert box.propose("add", "Answer in British English").id == p.id  # pending duplicates collapse
    done = box.approve(p.id, approval_event_id=42)
    assert done.status == "approved" and done.approval_event_id == 42
    text = prefs_text(home)
    assert "- Answer in British English <!-- since " in text and f"approval evt:42 · {p.id}" in text
    with pytest.raises(PrefsError, match="already approved"):
        box.approve(p.id, approval_event_id=43)
    r = box.propose("replace", "Answer in US English", old_text="British English")
    box.approve(r.id, approval_event_id=50)
    text = prefs_text(home)
    assert "British" not in text and "approval evt:50" in text
    d = box.propose("remove", old_text="US English")
    assert box.deny(d.id, reason="keep it").deny_reason == "keep it"
    assert "US English" in prefs_text(home)
    assert [x.status for x in box.proposals()] == ["denied", "approved", "approved"]
    assert [x.id for x in box.proposals("pending")] == []


def test_validation(home: Path) -> None:
    box = PrefsInbox(home)
    for args, msg in (
        (("add", ""), "needs content"),
        (("add", "two\nlines"), "one line"),
        (("add", "token sk-ant-abcdefghijklmnop"), "secret"),
        (("replace", "x"), "needs old_text"),
        (("bogus", "x"), "action must be"),
    ):
        with pytest.raises(PrefsError, match=msg):
            box.propose(*args)
    with pytest.raises(PrefsError, match="matches 0"):
        box.propose("remove", old_text="nothing like this")
    with pytest.raises(ProposalNotFound):
        box.deny("pref_missing")


def test_stale_old_text_is_refused_at_approval(home: Path) -> None:
    box = PrefsInbox(home)
    box.approve(box.propose("add", "Use metric units").id, approval_event_id=1)
    r = box.propose("remove", old_text="metric units")
    (home / "memory" / "PREFS.md").write_text("# Preferences\n")  # edited by hand meanwhile
    with pytest.raises(PrefsError, match="matches 0"):
        box.check_applicable(r.id)


def test_inbox_cap_expires_oldest_pending(home: Path) -> None:
    box = PrefsInbox(home, max_pending=2)
    ids = [box.propose("add", f"pref {i}").id for i in range(3)]
    statuses = {p.id: p.status for p in box.proposals()}
    assert statuses[ids[0]] == "denied" and statuses[ids[2]] == "pending"


# -------------------------------------------------------------- memory tool
async def _call(svc: Services, ctx: ToolContext, args: dict) -> tuple[str, bool]:
    out = await svc.coordinator.runner.executor.run(
        ToolCall("c1", "memory", json.dumps(args)),
        tools=svc.harness.tools,
        ctx=ctx,
        engine=ctx.services["engine"],
    )
    return out.result.content, out.result.is_error


async def test_memory_tool_target_prefs_only_proposes(services: Services, workdir: Path) -> None:
    events: list[tuple[str, dict]] = []

    async def emit(t: str, d: dict) -> None:
        events.append((t, d))

    ctx = ctx_for(services, workdir, emit=emit)
    text, err = await _call(
        services,
        ctx,
        {
            "operations": [
                {"action": "add", "target": "prefs", "content": "No emoji in replies", "reason": "asked"}
            ]
        },
    )
    assert not err, text
    assert "proposal pref_" in text and "approves" in text
    assert "No emoji" not in prefs_text(services.settings.home)  # never written directly
    pending = PrefsInbox(services.settings.home).proposals("pending")
    assert len(pending) == 1 and pending[0].session_id == "s1" and pending[0].reason == "asked"
    assert events[-1][0] == "prefs.proposed"
    # mixed batch: an invalid prefs op aborts the whole batch (USER.md untouched)
    text, err = await _call(
        services,
        ctx,
        {
            "operations": [
                {"action": "add", "target": "user", "content": "Lives in Lisbon"},
                {"action": "pin", "target": "prefs", "content": "x"},
            ]
        },
    )
    assert err and "target=prefs" in text
    assert "Lisbon" not in (services.settings.home / "memory" / "USER.md").read_text()
    text, err = await _call(
        services,
        ctx,
        {
            "operations": [
                {"action": "add", "target": "user", "content": "Lives in Lisbon"},
                {"action": "add", "target": "prefs", "content": "Times in 24h format"},
            ]
        },
    )
    assert not err and "USER.md updated" in text and "proposal" in text


# ---------------------------------------------------------------------- API
@pytest.fixture
async def client(services: Services) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(services.settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


async def test_prefs_api_approve_records_event_id(client: httpx.AsyncClient, services: Services) -> None:
    box = PrefsInbox(services.settings.home)
    p = box.propose("add", "Prefers pytest over unittest", session_id=None)
    r = await client.get("/api/memory/prefs/proposals", params={"status": "pending"})
    assert r.status_code == 200 and [x["id"] for x in r.json()] == [p.id]
    r = await client.post(f"/api/memory/prefs/proposals/{p.id}/approve")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved" and isinstance(body["approval_event_id"], int)
    evt = body["approval_event_id"]
    assert f"approval evt:{evt}" in prefs_text(services.settings.home)
    events = await services.events.replay(0)
    assert any(e.seq == evt and e.type == "prefs.approved" for e in events)
    assert (await client.post(f"/api/memory/prefs/proposals/{p.id}/approve")).status_code == 409
    assert (await client.post("/api/memory/prefs/proposals/pref_nope/deny", json={})).status_code == 404


async def test_prefs_api_create_and_deny(client: httpx.AsyncClient, services: Services) -> None:
    r = await client.post(
        "/api/memory/prefs/proposals", json={"action": "add", "content": "Short commit messages"}
    )
    assert r.status_code == 201 and r.json()["actor"] == "user"
    pid = r.json()["id"]
    r = await client.post(f"/api/memory/prefs/proposals/{pid}/deny", json={"reason": "not really"})
    assert r.json()["status"] == "denied" and r.json()["deny_reason"] == "not really"
    assert "Short commit" not in prefs_text(services.settings.home)
    bad = await client.post("/api/memory/prefs/proposals", json={"action": "remove", "old_text": "absent"})
    assert bad.status_code == 422


async def test_approved_prefs_reach_the_context_block(services: Services) -> None:
    box = PrefsInbox(services.settings.home)
    assert services.memory is not None
    assert "Always use tabs" not in await services.memory.context_block("hello", allow_aux=False)
    box.approve(box.propose("add", "Always use tabs").id, approval_event_id=7)
    assert "Always use tabs" in await services.memory.context_block("hello", allow_aux=False)
