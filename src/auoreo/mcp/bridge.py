"""MCP client bridge: every `mcp/<name>.yaml` server becomes a set of dynamic tools named
`mcp__<server>__<tool>` that go through the same executor and permission engine.

Each server connection lives in its own task (the SDK's context managers must enter and
exit in one task); tool calls from runner tasks go through the shared client session.

Resources are exposed through `list_resources` / `read_resource` (used by the
`list_mcp_resources` / `read_mcp_resource` tools). Elicitation (`mcp/elicitation.py`):
form mode becomes a question to the user of the session whose tool call is running
(secret fields refused); URL mode is a consent card + `mcp.elicitation.open` for the UI —
the user completes it in a browser, never us; a `-32042` tool error waits for
`notifications/elicitation/complete` and retries (≤3).
Sampling (`mcp/sampling.py`) is offered only to servers with `sampling.enabled`. A
per-tool `tools.<name>.timeoutMs` overrides the server's `timeout_ms`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from typing import TYPE_CHECKING, Any

from mcp.server import MCPServer, Server

from auoreo.harness.loader import ToolDef
from auoreo.harness.manifests import McpServerManifest, ToolManifest
from auoreo.harness.registry import ToolCollisionError
from auoreo.mcp.elicitation import (
    ElicitationFailed,
    call_with_elicitation,
    make_elicitation_callback,
    notification_handler,
)
from auoreo.mcp.sampling import SamplingHandler
from auoreo.tools.context import CURRENT_SESSION, ToolContext, ToolResult
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
    def __init__(self, manifest: McpServerManifest, target: Any, elicit: Any = None) -> None:
        self.manifest = manifest
        self.target = target
        self.elicit = elicit
        self.sample: Any = None
        self.on_message: Any = None
        self.last_session: str | None = None
        """Session of the latest tool call on this server (elicitation fallback when the
        server's request arrives in a task that does not carry `CURRENT_SESSION`)."""
        self.client: Client | None = None
        self.ready = asyncio.Event()
        self.stop = asyncio.Event()
        self.error: BaseException | None = None
        self.task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        try:
            # In-process servers only get a back-channel for server→client requests
            # (elicitation) on the legacy handshake; real transports negotiate (`auto`).
            in_process = isinstance(self.target, (MCPServer, Server))
            mode = "legacy" if in_process else "auto"
            async with Client(
                self.target,
                mode=mode,
                elicitation_callback=self.elicit,
                sampling_callback=self.sample,
                message_handler=self.on_message,
            ) as client:
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
        conn.elicit = make_elicitation_callback(self.s, manifest.name, lambda: conn.last_session)
        conn.on_message = notification_handler(self.s, manifest.name)
        if manifest.sampling.enabled:
            conn.sample = SamplingHandler(self.s, manifest, lambda: conn.last_session)
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
            override = manifest.tools.get(t.name)
            timeout_ms = (override.timeout_ms if override else None) or manifest.timeout_ms
            tm = ToolManifest(
                name=name,
                description=(t.description or t.name)[:1024],
                toolset=f"mcp:{manifest.name}",
                parameters=schema,
                permission=name,
                default_action="ask",
                exposure=manifest.exposure,
                timeout_ms=timeout_ms,
                search_hint=f"{manifest.name} {t.name}",
                net="proxy",
            )
            loader = self._make_loader(manifest.name, t.name, timeout_ms / 1000)
            try:
                self.s.harness.register_dynamic(ToolDef(tm, f"mcp:{manifest.name}", None, loader))
            except ToolCollisionError as exc:  # error_on_collision: skip, listed in harness errors
                log.error("MCP server '%s': %s", manifest.name, exc)
                continue
            names.append(name)
        await self.sync_skills(manifest.name)
        return names

    async def sync_skills(self, server: str) -> int:
        """Register the server's `skill://…` resources in the agent skill catalog (spec §15)."""
        from auoreo.harness.skill_sources import mcp_skill_defs

        skills = await mcp_skill_defs(self, server)
        self.s.harness.register_skills(f"mcp:{server}", skills)
        return len(skills)

    def _make_loader(self, server: str, remote: str, timeout_s: float | None = None) -> Any:
        async def handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
            conn = self._conns.get(server)
            if conn is None or conn.client is None:
                return ToolResult(f"MCP server '{server}' is not connected", is_error=True)
            conn.last_session = sid = CURRENT_SESSION.get() or ctx.session_id
            client = conn.client
            try:
                result = await call_with_elicitation(
                    self.s,
                    server,
                    sid,
                    lambda: client.call_tool(remote, args, read_timeout_seconds=timeout_s),
                )
            except ElicitationFailed as exc:
                return ToolResult(f"MCP server '{server}': {exc}", is_error=True)
            return ToolResult(_render_content(result), is_error=bool(result.is_error))

        return lambda: handler

    # ------------------------------------------------------------ resources
    def _client(self, server: str) -> Client:
        conn = self._conns.get(server)
        if conn is None or conn.client is None:
            known = ", ".join(sorted(self.connected())) or "none"
            raise KeyError(f"MCP server '{server}' is not connected (connected: {known})")
        if session := CURRENT_SESSION.get():
            conn.last_session = session
        return conn.client

    def connected(self) -> list[str]:
        return sorted(n for n, c in self._conns.items() if c.client is not None)

    async def list_resources(self, server: str) -> list[Any]:
        """Concrete resources of `server` (all pages)."""
        client = self._client(server)
        out: list[Any] = []
        cursor: str | None = None
        while True:
            page = await client.list_resources(cursor=cursor)
            out.extend(page.resources)
            cursor = page.next_cursor
            if not cursor:
                return out

    async def read_resource(self, server: str, uri: str) -> list[Any]:
        """Contents (`TextResourceContents | BlobResourceContents`) of one resource URI."""
        result = await self._client(server).read_resource(uri)
        return list(result.contents)

    async def disconnect(self, name: str) -> None:
        conn = self._conns.pop(name, None)
        self.s.harness.unregister_dynamic(f"{PREFIX}{name}__")
        self.s.harness.unregister_skills(f"mcp:{name}")
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
