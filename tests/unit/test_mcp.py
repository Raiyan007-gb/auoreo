from __future__ import annotations

import json
from pathlib import Path

from mcp.server import MCPServer

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import McpServerManifest, Rule
from auoreo.mcp.bridge import McpBridge
from auoreo.tools.context import ToolContext


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
