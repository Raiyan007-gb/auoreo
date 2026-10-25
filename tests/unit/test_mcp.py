from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.types import ElicitRequestURLParams
from pydantic import BaseModel

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import McpServerManifest, Rule
from auoreo.mcp.bridge import McpBridge
from auoreo.mcp.elicitation import coerce, form_questions, make_elicitation_callback
from auoreo.tools.context import CURRENT_SESSION, ToolContext


def demo_server() -> MCPServer:
    srv = MCPServer("demo")

    @srv.tool()
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    return srv


async def test_mcp_tools_register_and_execute_through_the_engine(services: Services, workdir: Path) -> None:
    bridge = McpBridge(services)
    services.mcp = bridge
    names = await bridge.connect(
        McpServerManifest(name="demo", command="x", exposure="direct"), demo_server()
    )
    assert names == ["mcp__demo__add"]
    tool = services.harness.tools["mcp__demo__add"]
    assert tool.manifest.default_action == "ask" and tool.source == "mcp:demo"
    ctx = ToolContext(session_id="s", cwd=workdir, home=services.settings.home)
    engine = PermissionEngine([Rule(permission="mcp__demo__*", pattern="*", action="allow")])
    out = await services.coordinator.runner.executor.run(
        ToolCall("c", "mcp__demo__add", json.dumps({"a": 2, "b": 40})),
        tools=services.harness.tools,
        ctx=ctx,
        engine=engine,
    )
    assert out.result.content == "42" and not out.result.is_error
    await bridge.stop()
    assert "mcp__demo__add" not in services.harness.tools


async def test_mcp_tool_used_in_a_turn(services: Services, gateway: FakeGateway, workdir: Path) -> None:
    bridge = McpBridge(services)
    services.mcp = bridge
    await bridge.connect(McpServerManifest(name="demo", command="x", exposure="direct"), demo_server())
    root = workdir / ".auoreo" / "permissions"
    root.mkdir(parents=True)
    (root / "mcp.yaml").write_text("rules:\n  - {permission: 'mcp__demo__*', pattern: '*', action: allow}\n")
    services.harness.reload()
    gateway.queue(Scripted(tool_calls=[("mcp__demo__add", {"a": 1, "b": 2})]), Scripted(text="3"))
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "add", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    hist = await services.coordinator.runner.load_history(sess.id)
    assert hist[2] == {"role": "tool", "tool_call_id": "call_1", "content": "3"}
    await bridge.stop()


# ------------------------------------------------------------- M5g: resources + elicitation
class Card(BaseModel):
    name: str
    copies: int


def rich_server() -> MCPServer:
    srv = MCPServer("rich")

    @srv.resource("docs://readme", name="readme", description="Project readme")
    def readme() -> str:
        return "# Hello from a resource"

    @srv.resource("docs://logo.png", name="logo", mime_type="image/png")
    def logo() -> bytes:
        return b"\x89PNG" + b"\0" * 96

    @srv.tool()
    async def issue_card(ctx: Context) -> str:
        """Ask the user for card details."""
        r = await ctx.elicit(message="Card details?", schema=Card)
        if r.action == "accept":
            return f"issued {r.data.copies}x {r.data.name}"
        return f"no card: {r.action}"

    @srv.tool()
    async def oauth(ctx: Context) -> str:
        """Needs a browser login."""
        r = await ctx.elicit_url(message="Log in", url="https://example.com/login", elicitation_id="e1")
        return f"oauth: {r.action}"

    return srv


async def _rich(services: Services) -> McpBridge:
    bridge = McpBridge(services)
    services.mcp = bridge
    await bridge.connect(McpServerManifest(name="rich", command="x", exposure="direct"), rich_server())
    return bridge


async def _exec(
    services: Services, workdir: Path, sid: str, name: str, args: dict[str, Any]
) -> tuple[str, bool]:
    ctx = ToolContext(
        session_id=sid, cwd=workdir, home=services.settings.home, services={"services": services}
    )
    engine = PermissionEngine([Rule(permission="*", pattern="*", action="allow")])
    out = await services.coordinator.runner.executor.run(
        ToolCall("c", name, json.dumps(args)), tools=services.harness.tools, ctx=ctx, engine=engine
    )
    return out.result.content, out.result.is_error


async def test_resource_round_trip_through_tools(services: Services, workdir: Path) -> None:
    bridge = await _rich(services)
    listed = await bridge.list_resources("rich")
    assert {r.uri for r in listed} == {"docs://readme", "docs://logo.png"}
    text, err = await _exec(services, workdir, "s", "list_mcp_resources", {})
    assert not err and "docs://readme (readme, text/plain) — Project readme" in text
    read = "read_mcp_resource"
    text, err = await _exec(services, workdir, "s", read, {"server": "rich", "uri": "docs://readme"})
    assert not err and text == "# Hello from a resource"
    text, err = await _exec(services, workdir, "s", read, {"server": "rich", "uri": "docs://logo.png"})
    assert not err and "binary resource docs://logo.png" in text and "image/png" in text
    text, err = await _exec(services, workdir, "s", read, {"server": "nope", "uri": "x://y"})
    assert err and "not connected" in text
    tool = services.harness.tools[read].manifest
    assert (tool.permission, tool.pattern_arg, tool.default_action) == ("read", "uri", "ask")
    await bridge.stop()


async def _answer_when_asked(services: Services, sid: str, responses: list[dict[str, Any]]) -> list[Any]:
    for _ in range(200):
        pending = services.questions.pending(sid)
        if pending:
            await services.questions.answer(pending[0].request_id, responses)
            return pending[0].questions
        await asyncio.sleep(0.02)
    raise AssertionError("no question was asked")


async def test_elicitation_form_accept(services: Services, workdir: Path) -> None:
    bridge = await _rich(services)
    answers = [
        {"question": "q0", "answers": ["Accept"], "answer": "Accept"},
        {"question": "q1", "answers": ["Visa"], "answer": "Visa"},
        {"question": "q2", "answers": ["2"], "answer": "2"},
    ]
    asker = asyncio.create_task(_answer_when_asked(services, "ses_e", answers))
    text, err = await _exec(services, workdir, "ses_e", "mcp__rich__issue_card", {})
    questions = await asker
    assert not err and text == "issued 2x Visa"
    assert "Card details?" in questions[0]["question"]
    assert [o["label"] for o in questions[0]["options"]] == ["Accept", "Decline"]
    assert [q["header"] for q in questions[1:]] == ["name", "copies"]
    await bridge.stop()


async def test_elicitation_form_decline_and_timeout(services: Services, workdir: Path) -> None:
    bridge = await _rich(services)
    decline = [{"question": "q0", "answers": ["Decline"], "answer": "Decline"}]
    asker = asyncio.create_task(_answer_when_asked(services, "ses_d", decline))
    text, _ = await _exec(services, workdir, "ses_d", "mcp__rich__issue_card", {})
    await asker
    assert text == "no card: decline"
    services.questions.timeout_s = 0.1
    text, _ = await _exec(services, workdir, "ses_d", "mcp__rich__issue_card", {})
    assert text == "no card: cancel"
    await bridge.stop()


async def test_elicitation_url_mode_is_announced_and_declined(services: Services, workdir: Path) -> None:
    sess = await services.create_session()
    cb = make_elicitation_callback(services, "rich", lambda: None)
    params = ElicitRequestURLParams(message="Log in", url="https://example.com/login", elicitation_id="e1")
    token = CURRENT_SESSION.set(sess.id)
    try:
        res = await cb(None, params)
    finally:
        CURRENT_SESSION.reset(token)
    assert res.action == "decline" and "browser" in (res.meta or {}).get("auoreo/message", "")
    evs = await services.events.replay(0, frozenset({f"session:{sess.id}"}))
    asked = [e for e in evs if e.type == "question.asked"]
    assert asked[0].data["mode"] == "url" and asked[0].data["url"] == "https://example.com/login"
    assert services.questions.pending(sess.id) == []  # nothing waits on the user
    # through a real (in-process) server as well
    bridge = await _rich(services)
    text, _ = await _exec(services, workdir, sess.id, "mcp__rich__oauth", {})
    assert text == "oauth: decline"
    await bridge.stop()


def test_form_questions_and_coercion() -> None:
    schema = {
        "properties": {
            "n": {"type": "integer", "title": "Count"},
            "ok": {"type": "boolean"},
            "tags": {"type": "array", "items": {"enum": ["a", "b"]}},
            "color": {"type": "string", "enum": ["red", "blue"]},
        },
        "required": ["n"],
    }
    qs = form_questions("srv", "Fill", schema)
    assert qs[1]["question"] == "Count (required)" and qs[2]["options"][0]["label"] == "yes"
    assert qs[3]["multiSelect"] is True and [o["label"] for o in qs[4]["options"]] == ["red", "blue"]
    responses = [
        {"answer": "Accept"},
        {"answer": "3"},
        {"answer": "yes"},
        {"answers": ["a", "b"], "answer": "a, b"},
        {"answer": "blue"},
    ]
    assert coerce(schema, responses) == {"n": 3, "ok": True, "tags": ["a", "b"], "color": "blue"}
