"""MCP client bridge: every `mcp/<name>.yaml` server becomes a set of dynamic tools named
`mcp__<server>__<tool>` that go through the same executor and permission engine.

Each server connection lives in its own task (the SDK's context managers must enter and
exit in one task); tool calls from runner tasks go through the shared client session.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from typing import TYPE_CHECKING, Any

from auoreo.harness.loader import ToolDef
from auoreo.harness.manifests import McpServerManifest, ToolManifest
from auoreo.tools.context import ToolContext, ToolResult
from mcp import Client, StdioServerParameters

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)
PREFIX = "mcp__"


def tool_name(server: str, tool: str) -> str:
    safe = re.sub(r"[^a-z0-9_]", "_", tool.lower())
    return f"{PREFIX}{server}__{safe}"[:64]


def _render_content(result: Any) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        parts.append(text if text is not None else f"[{type(block).__name__}]")
    if not parts and getattr(result, "structured_content", None) is not None:
        parts.append(str(result.structured_content))
    return "\n".join(parts) or "(no content)"


class _Connection:
    def __init__(self, manifest: McpServerManifest, target: Any) -> None:
        self.manifest = manifest
        self.target = target
        self.client: Client | None = None
        self.ready = asyncio.Event()
        self.stop = asyncio.Event()
        self.error: BaseException | None = None
        self.task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        try:
            async with Client(self.target) as client:
                self.client = client
                self.ready.set()
                await self.stop.wait()
        except Exception as exc:
            self.error = exc
            log.warning("MCP server '%s' failed: %s", self.manifest.name, exc)
        finally:
            self.client = None
            self.ready.set()


class McpBridge:
    def __init__(self, services: Services) -> None:
        self.s = services
        self._conns: dict[str, _Connection] = {}

    def _target(self, m: McpServerManifest) -> Any:
        env = {k: self._resolve(v) for k, v in m.env.items()}
        if m.transport == "stdio":
            return StdioServerParameters(command=m.command or "", args=m.args, env=env or None)
        if m.headers:
            import httpx2
            from mcp.client.streamable_http import streamable_http_client

            headers = {k: self._resolve(v) for k, v in m.headers.items()}
            return streamable_http_client(m.url or "", http_client=httpx2.AsyncClient(headers=headers))
        return m.url

    def _resolve(self, value: str) -> str:
        if value.startswith("secret:"):
            return self.s.secrets.get(value[7:]) or ""
        return value

    async def start(self) -> None:
        await self.sync()

    async def sync(self) -> None:
        """Connect newly enabled servers, drop removed/disabled ones."""
        wanted = {n: d.manifest for n, d in self.s.harness.snapshot.mcp.items() if d.manifest.enabled}
        for name in [n for n in self._conns if n not in wanted]:
            await self.disconnect(name)
        for name, manifest in wanted.items():
            if name not in self._conns:
                await self.connect(manifest, self._target(manifest))

    async def connect(self, manifest: McpServerManifest, target: Any, timeout: float = 20.0) -> list[str]:
        conn = _Connection(manifest, target)
        conn.task = asyncio.create_task(conn.run(), name=f"mcp:{manifest.name}")
        self._conns[manifest.name] = conn
        try:
            await asyncio.wait_for(conn.ready.wait(), timeout)
        except TimeoutError:
            log.warning("MCP server '%s' did not start within %ss", manifest.name, timeout)
            return []
        if conn.client is None:
            return []
        listing = await conn.client.list_tools()
        names: list[str] = []
        for t in listing.tools:
            name = tool_name(manifest.name, t.name)
            schema = dict(t.input_schema or {"type": "object", "properties": {}})
            schema.setdefault("type", "object")
            tm = ToolManifest(
                name=name,
                description=(t.description or t.name)[:1024],
                toolset=f"mcp:{manifest.name}",
                parameters=schema,
                permission=name,
                default_action="ask",
                exposure=manifest.exposure,
                timeout_ms=manifest.timeout_ms,
                search_hint=f"{manifest.name} {t.name}",
            )
            self.s.harness.register_dynamic(
                ToolDef(tm, f"mcp:{manifest.name}", None, self._make_loader(manifest.name, t.name))
            )
            names.append(name)
        return names

    def _make_loader(self, server: str, remote: str) -> Any:
        async def handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
            conn = self._conns.get(server)
            if conn is None or conn.client is None:
                return ToolResult(f"MCP server '{server}' is not connected", is_error=True)
            result = await conn.client.call_tool(remote, args)
            return ToolResult(_render_content(result), is_error=bool(result.is_error))

        return lambda: handler

    async def disconnect(self, name: str) -> None:
        conn = self._conns.pop(name, None)
        self.s.harness.unregister_dynamic(f"{PREFIX}{name}__")
        if conn is None:
            return
        conn.stop.set()
        if conn.task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(conn.task, 5)

    async def stop(self) -> None:
        for name in list(self._conns):
            await self.disconnect(name)

    def status(self) -> dict[str, str]:
        return {n: ("connected" if c.client else f"error: {c.error}") for n, c in self._conns.items()}
