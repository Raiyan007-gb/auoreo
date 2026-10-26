"""Creator mode: define → run (approval) → call → stop/undefine; nothing survives a restart."""

from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path
from typing import Any

from sqlalchemy import select

from auoreo.core.db import AuditRow
from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.plugins.creator import tool_name_for
from auoreo.tools.context import ToolContext, ToolResult

SID = "ses_abcdef1234567890"
CODE = """
async def run(args, ctx):
    return f"sum={args['a'] + args['b']} session={ctx.session_id}"
"""
PARAMS = {
    "type": "object",
    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
    "required": ["a", "b"],
}


def make_ctx(svc: Services, workdir: Path, *, sid: str = SID, **kw: Any) -> ToolContext:
    engine = PermissionEngine([Rule(permission="creator__*", pattern="*", action="allow")])
    return ToolContext(
        session_id=sid,
        cwd=workdir,
        home=svc.settings.home,
        harness=svc.harness,
        services={"services": svc, "engine": engine, **kw.pop("services", {})},
        **kw,
    )


async def call(svc: Services, ctx: ToolContext, tool: str, args: dict[str, Any]) -> ToolResult:
    engine = ctx.services["engine"]
    out = await svc.coordinator.runner.executor.run(
        ToolCall("c1", tool, json.dumps(args)), tools=svc.harness.tools, ctx=ctx, engine=engine
    )
    return out.result


async def test_define_run_call_stop_undefine(services: Services, workdir: Path) -> None:
    asked: list[tuple[str, str, str]] = []

    async def ask(permission: str, pattern: str, detail: str) -> bool:
        asked.append((permission, pattern, detail))
        return True

    ctx = make_ctx(services, workdir, ask=ask)
    await call(services, ctx, "plugin_inspect", {})
    res = await call(
        services,
        ctx,
        "plugin_define",
        {"name": "adder", "description": "adds", "parameters": PARAMS, "code": CODE},
    )
    assert not res.is_error, res.content
    tool = tool_name_for(SID, "adder")
    assert tool == "creator__abcdef12__adder"
    assert tool not in services.harness.tools  # inert until plugin_run
    path = services.settings.home / "sessions" / SID / "creator" / "adder.py"
    assert path.read_text() == CODE

    res = await call(services, ctx, "plugin_run", {"name": "adder"})
    assert not res.is_error, res.content
    assert asked and asked[0][0] == "plugin_run" and asked[0][1].startswith("adder@")
    assert "async def run" in asked[0][2]
    td = services.harness.tools[tool]
    assert td.manifest.default_action == "ask" and tool in ctx.state.loaded_tools

    res = await call(services, ctx, tool, {"a": 2, "b": 40})
    assert res.content == f"sum=42 session={SID}" and not res.is_error

    other = make_ctx(services, workdir, sid="ses_other000000000")
    res = await call(services, other, tool, {"a": 1, "b": 1})
    assert res.is_error and "another session" in res.content

    listing = await call(services, ctx, "plugin_inspect", {"query": "add"})
    rows = json.loads(listing.content)
    assert rows[0]["tool"] == tool and rows[0]["active"] is True and len(rows[0]["package_hash"]) == 64
    assert (await call(services, ctx, "plugin_inspect", {"query": "nothing"})).content.startswith(
        "no creator"
    )

    assert not (await call(services, ctx, "plugin_stop", {"name": "adder"})).is_error
    assert tool not in services.harness.tools
    assert not (await call(services, ctx, "plugin_undefine", {"name": "adder"})).is_error
    assert not path.exists()

    async with services.db.session() as s:
        rows_ = list((await s.execute(select(AuditRow).where(AuditRow.actor == "creator"))).scalars())
    actions = [r.action for r in rows_]
    assert actions == ["creator.define", "creator.run", "creator.stop", "creator.stop", "creator.undefine"]
    assert all(r.detail["session_id"] == SID and r.detail["plugin"] == "adder" for r in rows_)
    assert all(len(r.detail["package_hash"]) == 64 for r in rows_)


async def test_run_without_approval_is_refused(services: Services, workdir: Path) -> None:
    ctx = make_ctx(services, workdir, interactive=False)
    await call(services, ctx, "plugin_inspect", {})
    await call(services, ctx, "plugin_define", {"name": "adder", "description": "adds", "code": CODE})
    res = await call(services, ctx, "plugin_run", {"name": "adder"})
    assert res.is_error and "not approved" in res.content
    assert tool_name_for(SID, "adder") not in services.harness.tools


async def test_run_asks_through_the_approval_broker(services: Services, workdir: Path) -> None:
    ctx = make_ctx(services, workdir, interactive=True)
    await call(services, ctx, "plugin_inspect", {})
    await call(services, ctx, "plugin_define", {"name": "adder", "description": "adds", "code": CODE})

    async def approve_when_pending() -> None:
        while not services.approvals.pending(SID):
            await asyncio.sleep(0.01)
        p = services.approvals.pending(SID)[0]
        assert "async def run" in p.detail
        await services.approvals.reply(p.request_id, "once")

    responder = asyncio.create_task(approve_when_pending())
    res = await call(services, ctx, "plugin_run", {"name": "adder"})
    await responder
    assert not res.is_error, res.content
    assert tool_name_for(SID, "adder") in services.harness.tools


async def test_bad_code_and_non_creator_agents_are_rejected(services: Services, workdir: Path) -> None:
    ctx = make_ctx(services, workdir)
    await call(services, ctx, "plugin_inspect", {})
    res = await call(
        services, ctx, "plugin_define", {"name": "bad", "description": "x", "code": "def nope(:"}
    )
    assert res.is_error and "syntax error" in res.content
    res = await call(services, ctx, "plugin_define", {"name": "norun", "description": "x", "code": "x = 1\n"})
    assert res.is_error and "async def run" in res.content

    agent = types.SimpleNamespace(manifest=services.harness.agent("default").manifest)
    spec_ctx = make_ctx(services, workdir, services={"spec": types.SimpleNamespace(agent=agent)})
    res = await call(services, spec_ctx, "plugin_define", {"name": "adder", "description": "x", "code": CODE})
    assert res.is_error and "creator-mode only" in res.content
    creator = types.SimpleNamespace(manifest=services.harness.agent("creator").manifest)
    ok_ctx = make_ctx(services, workdir, services={"spec": types.SimpleNamespace(agent=creator)})
    res = await call(services, ok_ctx, "plugin_define", {"name": "adder", "description": "x", "code": CODE})
    assert not res.is_error, res.content


async def test_nothing_persists_across_a_restart(settings: Settings, workdir: Path) -> None:
    svc = await Services.create(
        settings, cwd=workdir, gateway=FakeGateway(), with_mcp=False, with_memory=False
    )

    async def ask(permission: str, pattern: str, detail: str) -> bool:
        return True

    ctx = make_ctx(svc, workdir, ask=ask)
    await call(svc, ctx, "plugin_inspect", {})
    await call(svc, ctx, "plugin_define", {"name": "adder", "description": "adds", "code": CODE})
    assert not (await call(svc, ctx, "plugin_run", {"name": "adder"})).is_error
    await svc.close()
    assert not (settings.home / "sessions" / SID / "creator").exists()

    fresh = await Services.create(
        settings, cwd=workdir, gateway=FakeGateway(), with_mcp=False, with_memory=False
    )
    try:
        assert not any(n.startswith("creator__") for n in fresh.harness.tools)
        res = await call(fresh, make_ctx(fresh, workdir), "plugin_inspect", {})
        assert res.content == "no creator tools defined in this session"
    finally:
        await fresh.close()
