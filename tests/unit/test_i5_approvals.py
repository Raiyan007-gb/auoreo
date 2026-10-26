"""I5: `allow{updated_input}` (spec §5.2) — broker, REST + WS reply, executor re-validation,
floors, audit — and `exec_command` `sandbox_permissions` + `justification` (§15/§19.8)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import select

from auoreo.api.app import create_app
from auoreo.backends.sandbox import permissions_request
from auoreo.core.approvals import ApprovalBroker, EditRefused, reply_frame
from auoreo.core.db import AuditRow, EventRow
from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.schema.ws import WsPermissionReply
from auoreo.tools.context import ToolContext
from auoreo.tools.edited_input import card_input, changed_keys

GOOD = "write build artifacts to /opt/cache/pip for the install step"


class Sink:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    async def __call__(self, sid: str, kind: str, data: dict[str, Any]) -> None:
        self.events.append((sid, kind, data))


async def _pending(broker: ApprovalBroker, sid: str = "s1") -> str:
    while not broker.pending(sid):
        await asyncio.sleep(0.005)
    return broker.pending(sid)[0].request_id


# ------------------------------------------------------------------ broker
async def test_broker_edit_needs_editable_and_allow() -> None:
    sink = Sink()
    broker = ApprovalBroker(sink, timeout_s=5)
    task = asyncio.create_task(
        broker.request("s1", "bash", "bash", "ls", "{}", editable=True, input={"command": "ls"})
    )
    rid = await _pending(broker)
    requested = next(d for _, k, d in sink.events if k == "permission.requested")
    assert requested["editable"] is True and requested["input"] == {"command": "ls"}
    with pytest.raises(EditRefused, match="allow reply"):
        await broker.reply(rid, "reject", updated_input={"command": "pwd"})
    assert await broker.reply(rid, "always", updated_input={"command": "pwd"})
    outcome = await task
    assert outcome.granted and outcome.reply == "once"  # edited calls never persist a grant
    assert outcome.updated_input == {"command": "pwd"}
    replied = [d for _, k, d in sink.events if k == "permission.replied"]
    assert replied == [{"request_id": rid, "reply": "once", "edited": True}]

    task2 = asyncio.create_task(broker.request("s1", "bash", "doom_loop", "bash", "loop?"))
    rid2 = await _pending(broker)
    requested2 = [d for _, k, d in sink.events if k == "permission.requested"][-1]
    assert "editable" not in requested2 and "input" not in requested2  # unchanged event shape
    with pytest.raises(EditRefused, match="does not accept"):
        await broker.reply(rid2, "once", updated_input={"x": 1})
    assert await broker.reply(rid2, "once")
    assert (await task2).updated_input is None


async def test_reply_frame_reports_refusal_as_detail(services: Services) -> None:
    task = asyncio.create_task(services.approvals.request("s9", "t", "doom_loop", "t", "d"))
    rid = await _pending(services.approvals, "s9")
    frame = WsPermissionReply.model_validate(
        {"kind": "permission", "reply": "once", "updatedInput": {"a": 1}}
    )
    assert frame.updated_input == {"a": 1}  # camelCase spec spelling accepted
    ok, why = await reply_frame(services, rid, frame)
    assert not ok and why == "this request does not accept edited input"
    ok, why = await reply_frame(services, rid, WsPermissionReply(kind="permission", reply="reject"))
    assert ok and why is None
    assert not (await task).granted


def test_card_input_redacts_and_caps(services: Services) -> None:
    secret = "sk-ant-api03-" + "A" * 40
    shown = card_input({"command": f"curl -H 'x-api-key: {secret}' x"}, services.settings)
    assert shown is not None and secret not in json.dumps(shown)
    services.settings.i5.approval_edit.max_input_bytes = 256
    assert card_input({"command": "x" * 1000}, services.settings) is None
    assert changed_keys({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 0}) == ["b", "c"]


# ------------------------------------------------------------------ executor
async def _ctx(services: Services, workdir: Path) -> ToolContext:
    sess = await services.create_session()
    return ToolContext(session_id=sess.id, cwd=workdir, home=services.settings.home, interactive=True)


async def _run(services: Services, ctx: ToolContext, tool: str, args: dict[str, Any]) -> Any:
    return await services.coordinator.runner.executor.run(
        ToolCall("c1", tool, json.dumps(args)),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine([]),
    )


async def _answer(services: Services, sid: str, **kw: Any) -> dict[str, Any]:
    rid = await _pending(services.approvals, sid)
    p = services.approvals.pending(sid)[0]
    card = {"editable": p.editable, "input": p.input, "justification": p.justification, "pattern": p.pattern}
    await services.approvals.reply(rid, kw.pop("reply", "once"), **kw)
    return card


async def _audit(services: Services, action: str) -> list[AuditRow]:
    async with services.db.session() as s:
        return list((await s.execute(select(AuditRow).where(AuditRow.action == action))).scalars())


async def test_executor_runs_the_edited_arguments(services: Services, workdir: Path) -> None:
    ctx = await _ctx(services, workdir)
    run = asyncio.create_task(_run(services, ctx, "bash", {"command": "echo original"}))
    card = await _answer(services, ctx.session_id, updated_input={"command": "echo edited-by-human"})
    out = await run
    assert card["editable"] and card["input"] == {"command": "echo original"}
    assert not out.result.is_error and "edited-by-human" in out.result.content
    rows = await _audit(services, "tool.input_edited")
    assert len(rows) == 1 and rows[0].decision == "allow"
    assert rows[0].detail["changed"] == ["command"] and "edited-by-human" not in json.dumps(rows[0].detail)


async def test_edit_is_revalidated_and_floored(services: Services, workdir: Path) -> None:
    ctx = await _ctx(services, workdir)
    run = asyncio.create_task(_run(services, ctx, "bash", {"command": "echo a"}))
    await _answer(services, ctx.session_id, updated_input={"command": 42})
    out = await run
    assert (
        out.result.is_error and "edit was refused" in out.result.content and "command" in out.result.content
    )

    run = asyncio.create_task(_run(services, ctx, "bash", {"command": "echo b"}))
    await _answer(services, ctx.session_id, updated_input={"command": "cat .env"})
    out = await run
    assert out.result.is_error and "edit was refused" in out.result.content
    denied = [r for r in await _audit(services, "tool.input_edited") if r.decision == "deny"]
    assert len(denied) == 1


# ------------------------------------------------------------------ exec_command escalation
@pytest.mark.parametrize(
    ("args", "expect"),
    [
        ({}, ("workspace-write", None, None)),
        ({"sandbox_permissions": "use_default"}, ("workspace-write", None, None)),
        ({"justification": GOOD}, ("workspace-write", None, "without sandbox_permissions")),
        ({"sandbox_permissions": "nope"}, ("workspace-write", None, "must be one of")),
        ({"sandbox_permissions": "require_escalated"}, ("workspace-write", None, "10–500")),
        (
            {"sandbox_permissions": "require_escalated", "justification": GOOD},
            ("workspace-write", "danger-full-access", None),
        ),
    ],
)
def test_permissions_request_table(args: dict[str, Any], expect: tuple[Any, Any, Any]) -> None:
    mode, wider, err = permissions_request("workspace-write", args)
    assert (mode, wider) == expect[:2]
    assert (err is None) if expect[2] is None else (err is not None and expect[2] in err)


async def test_exec_command_escalation_shows_and_audits_justification(
    services: Services, workdir: Path
) -> None:
    ctx = await _ctx(services, workdir)
    args = {"cmd": "echo hi", "sandbox_permissions": "require_escalated", "justification": GOOD}
    engine = PermissionEngine([])
    engine.grant_always("bash", "echo hi")
    run = asyncio.create_task(
        services.coordinator.runner.executor.run(
            ToolCall("c1", "exec_command", json.dumps(args)),
            tools=services.harness.tools,
            ctx=ctx,
            engine=engine,
        )
    )
    card = await _answer(services, ctx.session_id)
    out = await run
    assert card["justification"] == GOOD and card["pattern"].startswith("danger-full-access:")
    assert not card["editable"]  # one-shot escalation cards never take edited input
    assert not out.result.is_error, out.result.content
    [row] = await _audit(services, "sandbox.escalate")
    assert len(row.detail["justificationHash"]) == 16 and row.detail["toMode"] == "danger-full-access"
    assert GOOD not in json.dumps(row.detail)  # the audit keeps a hash; the card shows the text
    async with services.db.session() as s:
        evs = (await s.execute(select(EventRow).where(EventRow.type == "permission.requested"))).scalars()
        assert [e.data.get("justification") for e in evs] == [GOOD]


# ------------------------------------------------------------------ REST
@pytest.fixture
async def client(services: Services) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(services.settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


async def test_rest_reply_with_updated_input(client: httpx.AsyncClient, services: Services) -> None:
    task = asyncio.create_task(
        services.approvals.request("s2", "bash", "bash", "ls", "{}", editable=True, input={"command": "ls"})
    )
    rid = await _pending(services.approvals, "s2")
    pending = (await client.get("/api/permissions/pending")).json()
    assert pending[0]["editable"] is True and pending[0]["input"] == {"command": "ls"}
    r = await client.post(
        f"/api/permissions/{rid}/reply", json={"reply": "reject", "updated_input": {"a": 1}}
    )
    assert r.status_code == 422
    r = await client.post(
        f"/api/permissions/{rid}/reply", json={"reply": "once", "updatedInput": {"command": "pwd"}}
    )
    assert r.status_code == 200, r.text
    assert (await task).updated_input == {"command": "pwd"}
