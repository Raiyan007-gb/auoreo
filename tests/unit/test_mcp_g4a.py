"""G4a MCP: per-tool `timeoutMs` override, approval-gated sampling, and collision handling."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.types import SamplingMessage, TextContent
from sqlalchemy import select

from auoreo.core.db import AuditRow
from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import McpSampling, McpServerManifest, Rule
from auoreo.mcp.bridge import McpBridge
from auoreo.tools.context import ToolContext

ALLOW = [Rule(permission="*", pattern="*", action="allow")]


def sampler_server() -> MCPServer:
    srv = MCPServer("sampler")

    @srv.tool()
    async def summarize(text: str, ctx: Context) -> str:
        """Ask the client's model to summarize."""
        msg = SamplingMessage(role="user", content=TextContent(type="text", text=f"Summarize: {text}"))
        try:
            r = await ctx.session.create_message(messages=[msg], max_tokens=5000, system_prompt="Be brief.")
        except Exception as exc:
            return f"sampling error: {exc}"
        return f"model={r.model} text={getattr(r.content, 'text', '')}"

    @srv.tool()
    async def nap(seconds: float) -> str:
        """Sleep."""
        await asyncio.sleep(seconds)
        return "rested"

    return srv


async def _exec(
    services: Services, workdir: Path, sid: str, name: str, args: dict[str, Any]
) -> tuple[str, bool]:
    ctx = ToolContext(
        session_id=sid, cwd=workdir, home=services.settings.home, services={"services": services}
    )
    out = await services.coordinator.runner.executor.run(
        ToolCall("c", name, json.dumps(args)),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine(list(ALLOW)),
    )
    return out.result.content, out.result.is_error


def test_manifest_accepts_timeout_ms_alias() -> None:
    m = McpServerManifest.model_validate(
        {"name": "s", "command": "x", "tools": {"nap": {"timeoutMs": 2500}, "b": {"timeout_ms": 900}}}
    )
    assert m.tools["nap"].timeout_ms == 2500 and m.tools["b"].timeout_ms == 900
    assert m.sampling.enabled is False


async def test_per_tool_timeout_override(services: Services, workdir: Path) -> None:
    bridge = McpBridge(services)
    services.mcp = bridge
    manifest = McpServerManifest.model_validate(
        {"name": "sampler", "command": "x", "exposure": "direct", "tools": {"nap": {"timeoutMs": 300}}}
    )
    await bridge.connect(manifest, sampler_server())
    tools = services.harness.tools
    assert tools["mcp__sampler__nap"].manifest.timeout_ms == 300
    assert tools["mcp__sampler__summarize"].manifest.timeout_ms == manifest.timeout_ms
    text, err = await _exec(services, workdir, "s", "mcp__sampler__nap", {"seconds": 2})
    assert err and "timed out after 300 ms" in text
    await bridge.stop()


async def _audit(services: Services) -> list[AuditRow]:
    async with services.db.session() as s:
        rows = (await s.execute(select(AuditRow).where(AuditRow.action == "mcp.sampling"))).scalars()
        return list(rows)


async def test_sampling_disabled_by_default(services: Services, workdir: Path) -> None:
    bridge = McpBridge(services)
    services.mcp = bridge
    await bridge.connect(McpServerManifest(name="sampler", command="x", exposure="direct"), sampler_server())
    text, _ = await _exec(services, workdir, "s", "mcp__sampler__summarize", {"text": "hello"})
    assert text.startswith("sampling error")
    await bridge.stop()


async def test_sampling_is_approval_gated_and_routed_through_the_gateway(
    services: Services, workdir: Path
) -> None:
    bridge = McpBridge(services)
    services.mcp = bridge
    manifest = McpServerManifest(
        name="sampler", command="x", exposure="direct", sampling=McpSampling(enabled=True, max_tokens=64)
    )
    await bridge.connect(manifest, sampler_server())

    # Unknown session / nobody to approve → refused, audited as a deny.
    text, _ = await _exec(services, workdir, "ses_ghost", "mcp__sampler__summarize", {"text": "hello"})
    assert "sampling error" in text and "non-interactive" in text

    # A session whose policy allows `mcp_sampling` for this server.
    row = await services.create_session()
    rt = await services.coordinator.runtime(row.id)
    rt.engine = PermissionEngine([Rule(permission="mcp_sampling", pattern="sampler", action="allow")])
    text, err = await _exec(services, workdir, row.id, "mcp__sampler__summarize", {"text": "hello"})
    assert not err and text.startswith("model=") and "sampling error" not in text
    req = [r for r in services.gateway.requests if r.metadata.get("purpose") == "sampling"][-1]  # type: ignore[attr-defined]
    assert req.max_tokens == 64 and req.tool_choice == "none"
    assert req.messages[0] == {"role": "system", "content": "Be brief."}
    assert req.messages[1]["content"] == "Summarize: hello"
    rows = await _audit(services)
    assert [r.decision for r in rows] == ["deny", "allow"]
    detail = services.audit.plain_detail(rows[-1])
    assert detail["server"] == "sampler" and detail["tokens"] > 0
    usage = await services.coordinator.runner.usage.by_session(row.id)
    assert any(u.run_id == "mcp_sampling" for u in usage)

    # A deny rule wins even for an otherwise interactive session.
    row2 = await services.create_session()
    rt2 = await services.coordinator.runtime(row2.id)
    rt2.engine = PermissionEngine([Rule(permission="mcp_sampling", pattern="*", action="deny")])
    text, _ = await _exec(services, workdir, row2.id, "mcp__sampler__summarize", {"text": "x"})
    assert "denied by policy" in text
    await bridge.stop()


async def test_sampling_asks_the_human_once_per_session(services: Services, workdir: Path) -> None:
    bridge = McpBridge(services)
    services.mcp = bridge
    manifest = McpServerManifest(
        name="sampler", command="x", exposure="direct", sampling=McpSampling(enabled=True)
    )
    await bridge.connect(manifest, sampler_server())
    row = await services.create_session()
    rt = await services.coordinator.runtime(row.id)
    rt.engine = PermissionEngine([])
    asked: list[str] = []

    async def approve() -> None:
        for _ in range(250):
            for p in services.approvals.pending(row.id):
                asked.append(p.permission)
                await services.approvals.reply(p.request_id, "once")
                return
            await asyncio.sleep(0.02)

    approver = asyncio.create_task(approve())
    text, err = await _exec(services, workdir, row.id, "mcp__sampler__summarize", {"text": "one"})
    await approver
    assert not err and text.startswith("model=") and asked == ["mcp_sampling"]
    text, err = await _exec(services, workdir, row.id, "mcp__sampler__summarize", {"text": "two"})
    assert not err and text.startswith("model=") and asked == ["mcp_sampling"]  # no second prompt
    await bridge.stop()
