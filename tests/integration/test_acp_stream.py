"""F3: ACP `session/prompt` streaming (`Accept: text/event-stream`), set_mode, handshake, `_` methods."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import select

from auoreo.api.app import create_app
from auoreo.api.routers import acp
from auoreo.core.db import AuditRow
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway, Scripted

SSE = {"Accept": "text/event-stream"}


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=20) as c,
    ):
        yield c


def rpc(method: str, params: dict[str, Any] | None = None, rid: int | None = 1) -> dict[str, Any]:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if rid is not None:
        msg["id"] = rid
    return msg


async def result(c: httpx.AsyncClient, method: str, params: dict[str, Any] | None = None) -> Any:
    r = await c.post("/acp", json=rpc(method, params))
    assert r.status_code == 200, r.text
    return r.json().get("result", r.json().get("error"))


def frames(text: str) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


async def audit(services: Services, action: str) -> list[AuditRow]:
    async with services.db.session() as s:
        return list((await s.execute(select(AuditRow).where(AuditRow.action == action))).scalars())


async def test_stream_order_with_tool_call(
    client: httpx.AsyncClient, gateway: FakeGateway, workdir: Path
) -> None:
    (workdir / "notes.txt").write_text("alpha\n")
    sid = (await result(client, "session/new", {"cwd": str(workdir)}))["sessionId"]
    gateway.queue(
        Scripted(
            text="Reading.",
            tool_calls=[
                ("read", {"path": str(workdir / "notes.txt")}),
                ("read", {"path": str(workdir / "nope")}),
            ],
        ),
        Scripted(text="All done."),
    )
    body = {"sessionId": sid, "prompt": [{"type": "text", "text": "read it"}]}
    r = await client.post("/acp", json=rpc("session/prompt", body, rid=7), headers=SSE)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    out = frames(r.text)
    final, notes = out[-1], out[:-1]
    assert final["id"] == 7 and final["result"]["stopReason"] == "end_turn"
    assert all(n["method"] == "session/update" and n["params"]["sessionId"] == sid for n in notes)
    updates = [n["params"]["update"] for n in notes]
    kinds = [u["sessionUpdate"] for u in updates]
    assert kinds[0] == "agent_message_chunk" and kinds[-1] == "agent_message_chunk"
    text = "".join(u["content"]["text"] for u in updates if u["sessionUpdate"] == "agent_message_chunk")
    assert "Reading." in text and "All done." in text

    calls = [u for u in updates if u["sessionUpdate"] == "tool_call"]
    assert len(calls) == 2 and all(c["status"] == "pending" and c["kind"] == "read" for c in calls)
    assert calls[0]["rawInput"] == {"path": str(workdir / "notes.txt")}
    for call in calls:
        seq = [
            u["status"]
            for u in updates
            if u.get("toolCallId") == call["toolCallId"]
            and u["sessionUpdate"] in {"tool_call", "tool_call_update"}
        ]
        assert seq[0] == "pending" and seq[1] == "in_progress" and seq[-1] in {"completed", "failed"}
    statuses = {
        u["toolCallId"]: u["status"]
        for u in updates
        if u["sessionUpdate"] == "tool_call_update" and u["status"] in {"completed", "failed"}
    }
    assert sorted(statuses.values()) == ["completed", "failed"]
    first_done = kinds.index("tool_call_update", kinds.index("tool_call") + 2)
    assert kinds.index("tool_call") < first_done < len(kinds) - 1
    assert acp.stats()["streams"] >= 1


async def test_stream_errors_and_plain_unchanged(client: httpx.AsyncClient, gateway: FakeGateway) -> None:
    r = await client.post(
        "/acp", json=rpc("session/prompt", {"sessionId": "nope", "prompt": []}), headers=SSE
    )
    out = frames(r.text)
    assert len(out) == 1 and out[0]["error"]["code"] == -32602

    sid = (await result(client, "session/new"))["sessionId"]
    gateway.queue(Scripted(text="plain"))
    r = await client.post(
        "/acp", json=rpc("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "x"}]})
    )
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["result"]["_meta"]["auoreo"]["text"] == "plain"


async def test_plan_updates_stream(
    client: httpx.AsyncClient, gateway: FakeGateway, services: Services
) -> None:
    sid = (await result(client, "session/new"))["sessionId"]
    from auoreo.api.routers.acp import session_updates
    from auoreo.schema.events import ServerEvent

    ev = ServerEvent(
        id="1",
        seq=1,
        type="plan.updated",
        aggregate_id=sid,
        aggregate_seq=1,
        data={"steps": [{"step": "a", "status": "completed"}, {"step": "b", "status": "pending"}]},
        created_at="2026-01-01T00:00:00Z",  # type: ignore[arg-type]
    )
    (note,) = session_updates(sid, ev)
    assert note["params"]["update"] == {
        "sessionUpdate": "plan",
        "entries": [
            {"content": "a", "priority": "medium", "status": "completed"},
            {"content": "b", "priority": "medium", "status": "pending"},
        ],
    }
    assert session_updates(sid, ev.model_copy(update={"type": "run.started"})) == []


async def test_set_mode(client: httpx.AsyncClient, services: Services) -> None:
    new = await result(client, "session/new")
    sid = new["sessionId"]
    assert {m["id"] for m in new["modes"]["availableModes"]} == {"manual", "acceptEdits", "plan"}
    assert await result(client, "session/set_mode", {"sessionId": sid, "modeId": "acceptEdits"}) == {}
    rt = await services.coordinator.runtime(sid)
    assert isinstance(rt.engine, acp.AcpModeEngine) and rt.engine.mode == "acceptEdits"
    assert rt.engine.evaluate("edit", "/tmp/x.txt", "ask", interactive=False).action == "allow"
    rt.engine.mode = "manual"  # what the runtime does at the next turn start …
    rt.engine.__post_init__()  # … followed by this
    assert rt.engine.mode == "acceptEdits"

    assert await result(client, "session/set_mode", {"sessionId": sid, "modeId": "plan"}) == {}
    assert rt.state.plan_mode and rt.engine.mode == "plan"
    assert await result(client, "session/set_mode", {"sessionId": sid, "modeId": "manual"}) == {}
    assert not rt.state.plan_mode and rt.engine.mode == "manual"

    bad = await result(client, "session/set_mode", {"sessionId": sid, "modeId": "yolo"})
    assert bad["code"] == -32602
    assert len(await audit(services, "acp.session.set_mode")) == 3


async def test_handshake_audited(client: httpx.AsyncClient, services: Services) -> None:
    caps = {"fs": {"readTextFile": True}, "terminal": False}
    init = {"protocolVersion": 1, "clientCapabilities": caps, "clientInfo": {"name": "zed", "version": "1"}}
    res = await result(client, "initialize", init)
    assert res["_meta"]["auoreo"]["extMethods"] == []
    r = await client.post("/acp", json=rpc("session/new"), headers={"ACP-Protocol-Version": "1"})
    sid = r.json()["result"]["sessionId"]
    (row,) = await audit(services, "acp.handshake")
    detail = services.audit.plain_detail(row)
    assert row.session_id == sid
    assert detail["clientProtocolVersion"] == 1 and detail["headerVersion"] == "1"
    assert detail["clientCapabilities"] == ["fs", "terminal"] and detail["clientInfo"]["name"] == "zed"


async def test_unknown_underscore_methods(client: httpx.AsyncClient, services: Services) -> None:
    before = acp.stats()["ext_methods"].get("_zed/ping", 0)
    r = await client.post("/acp", json=rpc("_zed/ping", rid=None))
    assert r.status_code == 204
    r = await client.post("/acp", json=rpc("_zed/ping"))
    assert r.json()["error"]["code"] == -32601
    assert acp.stats()["ext_methods"]["_zed/ping"] == before + 2
    assert any(
        services.audit.plain_detail(a)["method"] == "_zed/ping"
        for a in await audit(services, "acp.ext.ignored")
    )
