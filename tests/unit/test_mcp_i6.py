"""I6 MCP client: `tools/list_changed` → re-list + generation bump + event, `roots` (session cwd
+ allow-list as `file://`, `roots/list_changed` on cwd change), legacy SSE transport, sampling
with tools (iteration cap 3, `toolChoice: none` on the last pass), `modelPreferences` → aux
model, and the P0 bundles (`mcp add --bundle`) (spec §15 L664, §25.15 L1072-L1075, §26.14)."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.types import (
    ModelHint,
    ModelPreferences,
    SamplingMessage,
    TextContent,
    Tool,
    ToolChoice,
    ToolResultContent,
    ToolUseContent,
)

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.types import ToolCall
from auoreo.harness.discovery import Candidate, Root
from auoreo.harness.loader import load_mcp
from auoreo.harness.manifests import McpRoots, McpSampling, McpServerManifest, Rule
from auoreo.mcp.bridge import McpBridge
from auoreo.mcp.client_extras import RootsState, root_uris
from auoreo.mcp.sampling_tools import select_model, to_messages, to_result
from auoreo.tools.context import ToolContext

ALLOW = [Rule(permission="*", pattern="*", action="allow")]
REPO = Path(__file__).resolve().parents[2]


async def _exec(services: Services, cwd: Path, sid: str, name: str, args: dict[str, Any]) -> tuple[str, bool]:
    ctx = ToolContext(session_id=sid, cwd=cwd, home=services.settings.home, services={"services": services})
    out = await services.coordinator.runner.executor.run(
        ToolCall("c", name, json.dumps(args)),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine(list(ALLOW)),
    )
    return out.result.content, out.result.is_error


def _events(services: Services) -> list[tuple[str, dict[str, Any]]]:
    seen: list[tuple[str, dict[str, Any]]] = []
    emit = services.emit

    async def record(aggregate: str, kind: str, data: dict[str, Any]) -> None:
        seen.append((kind, data))
        await emit(aggregate, kind, data)

    services.emit = record  # type: ignore[method-assign]
    return seen


async def _until(pred: Any, timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


# ------------------------------------------------------------------ list_changed
def growing_server() -> MCPServer:
    srv = MCPServer("grow")

    @srv.tool()
    async def grow(ctx: Context) -> str:
        """Add a tool and tell the client."""

        def extra(x: int) -> int:
            """A tool that appeared later."""
            return x * 2

        srv.add_tool(extra, name="extra")
        await ctx.session.send_tool_list_changed()
        return "grown"

    return srv


async def test_tools_list_changed_relists_bumps_generation_and_emits(
    services: Services, workdir: Path
) -> None:
    services.settings.i6.mcp.refresh_debounce_s = 0.0
    seen = _events(services)
    bridge = McpBridge(services)
    services.mcp = bridge
    names = await bridge.connect(
        McpServerManifest(name="grow", command="x", exposure="direct"), growing_server()
    )
    assert names == ["mcp__grow__grow"]
    gen = services.harness.generation
    text, err = await _exec(services, workdir, "s", "mcp__grow__grow", {})
    assert not err and text == "grown"
    await _until(lambda: "mcp__grow__extra" in services.harness.tools)
    assert services.harness.generation > gen
    changed = [d for k, d in seen if k == "mcp.tools_changed"]
    assert changed and changed[-1]["server"] == "grow" and "mcp__grow__extra" in changed[-1]["tools"]
    text, err = await _exec(services, workdir, "s", "mcp__grow__extra", {"x": 21})
    assert not err and text == "42"
    await bridge.stop()


# ------------------------------------------------------------------ roots
def roots_server() -> MCPServer:
    srv = MCPServer("rooty")

    @srv.tool()
    async def where(ctx: Context) -> str:
        """List the client's roots."""
        res = await ctx.session.list_roots()
        return " ".join(str(r.uri) for r in res.roots)

    return srv


async def test_roots_are_session_cwd_plus_allow_list(
    services: Services, workdir: Path, tmp_path: Path
) -> None:
    extra = tmp_path / "shared"
    extra.mkdir()
    bridge = McpBridge(services)
    services.mcp = bridge
    roots = McpRoots(enabled=True, paths=[str(extra), str(tmp_path / "missing"), "relative/dir"])
    await bridge.connect(
        McpServerManifest(name="rooty", command="x", exposure="direct", roots=roots), roots_server()
    )
    text, err = await _exec(services, workdir, "s", "mcp__rooty__where", {})
    assert not err and text.split() == [workdir.resolve().as_uri(), extra.resolve().as_uri()]  # noqa: ASYNC240
    other = tmp_path / "other"
    other.mkdir()  # noqa: ASYNC240
    text, _ = await _exec(services, other, "s", "mcp__rooty__where", {})
    assert text.split()[0] == other.resolve().as_uri()
    await bridge.stop()


async def test_roots_off_by_default(services: Services, workdir: Path) -> None:
    bridge = McpBridge(services)
    services.mcp = bridge
    await bridge.connect(McpServerManifest(name="rooty", command="x", exposure="direct"), roots_server())
    text, _ = await _exec(services, workdir, "s", "mcp__rooty__where", {})
    assert "file://" not in text  # no roots capability declared: the server's request fails
    await bridge.stop()


async def test_roots_list_changed_sent_when_cwd_changes(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (tmp_path / "link").symlink_to(b)
    sent: list[int] = []
    client = SimpleNamespace(send_roots_list_changed=lambda: _count(sent))
    state = RootsState([])
    assert await state.note_cwd(a, client) is False  # the server never asked yet
    listing = await state.callback()
    assert [str(r.uri) for r in listing.roots] == [a.resolve().as_uri()]
    assert await state.note_cwd(a, client) is False  # unchanged
    assert await state.note_cwd(tmp_path / "link", client) is True and sent == [1]
    assert state.sent == (b.resolve().as_uri(),)  # symlink resolved to its real target
    assert root_uris(None, ["~nonexistent-user/x"]) == ()


async def _count(sent: list[int]) -> None:
    sent.append(1)


# ------------------------------------------------------------------ SSE transport
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def sse_url() -> Iterator[str]:
    import uvicorn

    srv = MCPServer("legacy")

    @srv.tool()
    def ping(word: str) -> str:
        """Echo over SSE."""
        return f"pong {word}"

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(srv.sse_app(), host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    end = time.monotonic() + 10
    while not server.started and time.monotonic() < end:
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}/sse"
    finally:
        server.should_exit = True
        thread.join(5)


async def test_sse_transport_end_to_end(
    services: Services, workdir: Path, sse_url: str, tmp_path: Path
) -> None:
    path = tmp_path / "legacy.yaml"
    path.write_text(f"name: legacy\ntransport: sse\nurl: {sse_url}\nexposure: direct\n", encoding="utf-8")
    manifest = load_mcp(Candidate("mcp", "legacy", path, Root("runtime", tmp_path))).manifest
    assert manifest.transport == "sse"
    bridge = McpBridge(services)
    services.mcp = bridge
    names = await bridge.connect(manifest, bridge._target(manifest))
    assert names == ["mcp__legacy__ping"], bridge.status()
    text, err = await _exec(services, workdir, "s", "mcp__legacy__ping", {"word": "hi"})
    assert not err and text == "pong hi"
    await bridge.stop()


def test_sse_requires_url(tmp_path: Path) -> None:
    from auoreo.harness.loader import LoadError

    path = tmp_path / "x.yaml"
    path.write_text("name: x\ntransport: sse\n", encoding="utf-8")
    with pytest.raises(LoadError, match="sse transport requires 'url'"):
        load_mcp(Candidate("mcp", "x", path, Root("runtime", tmp_path)))


# ------------------------------------------------------------------ sampling with tools
LOOKUP = Tool(name="lookup", description="Look something up.", input_schema={"type": "object"})


def tool_sampler() -> MCPServer:
    srv = MCPServer("toolsamp")

    @srv.tool()
    async def agent(rounds: int, ctx: Context) -> str:
        """Run `rounds` sampling passes, each allowing the `lookup` tool."""
        out = []
        for _ in range(rounds):
            msg = SamplingMessage(
                role="user", content=TextContent(type="text", text='/tool lookup {"q": "x"}')
            )
            try:
                r = await ctx.session.create_message(
                    messages=[msg], max_tokens=200, tools=[LOOKUP], tool_choice=ToolChoice(mode="auto")
                )
            except Exception as exc:
                out.append(f"error:{exc}")
                continue
            kinds = [b.type for b in r.content_as_list]
            uses = [b for b in r.content_as_list if b.type == "tool_use"]
            out.append(f"{r.stop_reason}:{','.join(kinds)}" + (f":{uses[0].name}" if uses else ""))
        return " | ".join(out)

    return srv


async def test_sampling_tool_use_iteration_cap_and_tool_choice_none(
    services: Services, workdir: Path
) -> None:
    bridge = McpBridge(services)
    services.mcp = bridge
    manifest = McpServerManifest(
        name="toolsamp", command="x", exposure="direct", sampling=McpSampling(enabled=True)
    )
    await bridge.connect(manifest, tool_sampler())
    row = await services.create_session()
    rt = await services.coordinator.runtime(row.id)
    rt.engine = PermissionEngine([Rule(permission="mcp_sampling", pattern="toolsamp", action="allow")])
    text, err = await _exec(services, workdir, row.id, "mcp__toolsamp__agent", {"rounds": 4})
    assert not err, text
    parts = text.split(" | ")
    assert parts[0] == "toolUse:tool_use:lookup" and parts[1] == "toolUse:tool_use:lookup"
    assert parts[2] == "endTurn:text"  # last pass: toolChoice none, no tools
    assert parts[3].startswith("error:") and "iteration limit" in parts[3]
    reqs = [r for r in services.gateway.requests if r.metadata.get("purpose") == "sampling"]  # type: ignore[attr-defined]
    assert [r.metadata["pass"] for r in reqs] == [1, 2, 3]
    assert (
        reqs[0].tools and reqs[0].tools[0]["function"]["name"] == "lookup" and reqs[0].tool_choice == "auto"
    )
    assert reqs[2].tools == () and reqs[2].tool_choice == "none"
    # A new tool call gets a fresh budget.
    text, _ = await _exec(services, workdir, row.id, "mcp__toolsamp__agent", {"rounds": 1})
    assert text == "toolUse:tool_use:lookup"
    await bridge.stop()


def test_to_messages_maps_tool_use_and_tool_result() -> None:
    params = SimpleNamespace(
        system_prompt="sys",
        messages=[
            SamplingMessage(role="user", content=TextContent(type="text", text="find x")),
            SamplingMessage(
                role="assistant",
                content=[
                    TextContent(type="text", text="looking"),
                    ToolUseContent(type="tool_use", id="t1", name="lookup", input={"q": "x"}),
                ],
            ),
            SamplingMessage(
                role="user",
                content=[
                    ToolResultContent(
                        type="tool_result", tool_use_id="t1", content=[TextContent(type="text", text="42")]
                    )
                ],
            ),
        ],
    )
    msgs = to_messages(params)
    assert msgs[0] == {"role": "system", "content": "sys"}
    assert msgs[1] == {"role": "user", "content": "find x"}
    assert msgs[2]["role"] == "assistant" and msgs[2]["content"] == "looking"
    assert msgs[2]["tool_calls"][0]["function"] == {"name": "lookup", "arguments": '{"q": "x"}'}
    assert msgs[3] == {"role": "tool", "tool_call_id": "t1", "content": "42"}
    res = to_result("", [ToolCall("c9", "lookup", "not json")], "m", True, "tool_calls")
    assert res.stop_reason == "toolUse" and res.content[0].input == {}  # type: ignore[index,union-attr]
    plain = to_result("hi", [], "m", False, "stop")
    assert plain.content.text == "hi"  # type: ignore[union-attr]


def test_model_preferences_map_onto_aux_selection(home: Path) -> None:
    st = Settings(
        home=home,
        gateway="fake",
        models={
            "default": {"models": ["openrouter/anthropic/claude-sonnet-5"]},
            "small": {"models": ["openrouter/google/gemini-flash"]},
            "smart": {"models": ["openrouter/openai/gpt-6"]},
        },
        model="default",
    )
    gw = SimpleNamespace(has_credentials=lambda m: True, supports_vision=lambda m: True)
    hint = ModelPreferences(hints=[ModelHint(name="gemini")])
    assert select_model(st, gw, hint) == "openrouter/google/gemini-flash"
    main_hint = ModelPreferences(hints=[ModelHint(name="default")], cost_priority=0.9)
    assert select_model(st, gw, main_hint) == "small"  # the main model is never picked by a hint
    assert select_model(st, gw, ModelPreferences(intelligence_priority=0.9, cost_priority=0.1)) == "smart"
    assert select_model(st, gw, ModelPreferences(speed_priority=0.8)) == "small"
    weak = ModelPreferences(cost_priority=0.2)
    assert select_model(st, gw, weak) == select_model(st, gw, None)  # → plain aux routing


# ------------------------------------------------------------------ bundles
@pytest.mark.parametrize("name", ["filesystem", "time", "memory", "fetch"])
def test_bundles_are_valid_and_off_by_default(name: str) -> None:
    path = REPO / "mcp" / f"{name}.yaml"
    m = load_mcp(Candidate("mcp", name, path, Root("bundled", REPO))).manifest
    assert not m.enabled and m.transport == "stdio" and m.command in {"uvx", "npx"}
    assert m.roots.enabled is (name == "filesystem")


def test_mcp_add_bundle_writes_an_enabled_runtime_copy(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from auoreo.cli.app import app

    monkeypatch.setenv("AUOREO_HOME", str(home))
    monkeypatch.setattr("auoreo.cli.commands.mcp._reload", lambda: None)
    runner = CliRunner()
    res = runner.invoke(app, ["mcp", "add", "--bundle", "time", "--no-check"])
    assert res.exit_code == 0, res.output
    written = home / "mcp" / "time.yaml"
    m = load_mcp(Candidate("mcp", "time", written, Root("runtime", home))).manifest
    assert m.enabled and m.args == ["mcp-server-time"]
    res = runner.invoke(app, ["mcp", "add", "--bundle", "nope", "--no-check"])
    assert res.exit_code != 0 and "no bundled MCP server" in res.output
    res = runner.invoke(app, ["mcp", "add", "--bundle", "time", "--url", "http://x", "--no-check"])
    assert res.exit_code != 0
