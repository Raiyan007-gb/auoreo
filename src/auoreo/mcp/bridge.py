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
Sampling (`mcp/sampling.py`) is offered only to servers with `sampling.enabled` (tool use
inside sampling declared via `sampling.tools`). A per-tool `tools.<name>.timeoutMs` overrides
the server's `timeout_ms`. I6 (`mcp/client_extras.py`): `tools/list_changed` → re-list +
generation bump + `mcp.tools_changed`; `roots` (cwd + allow-list, `roots/list_changed` when
the calling cwd changes); legacy `transport: sse`.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp.server import MCPServer, Server
from mcp.types import SamplingCapability, SamplingToolsCapability

from auoreo.harness.loader import ToolDef
from auoreo.harness.manifests import McpServerManifest, ToolManifest
from auoreo.harness.registry import ToolCollisionError
from auoreo.mcp.client_extras import (
    Debouncer,
    RootsState,
    listen_tools_changed,
    sse_target,
    with_tools_changed,
)
from auoreo.mcp.elicitation import (
    ElicitationFailed,
    call_with_elicitation,
    make_elicitation_callback,
    notification_handler,
)
from auoreo.mcp.sampling import SamplingHandler
from auoreo.tools.context import CURRENT_SESSION, ToolContext, ToolResult
from auoreo.tools.egress import session_proxy
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
        self.roots: RootsState | None = None
        self.listen: Any = None
        """`on_change` callback for a `subscriptions/listen` stream (None = don't listen)."""
        self.call_id: str | None = None
        """Tool call currently running on this server (sampling iteration budget key)."""
        self.ready = asyncio.Event()
        self.stop = asyncio.Event()
        self.error: BaseException | None = None
        self.task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        try:
            # In-process servers only get a back-channel for server→client requests
            # (elicitation) on the legacy handshake; real transports negotiate (`auto`).
            in_process = isinstance(self.target, (MCPServer, Server))
            legacy = in_process or self.manifest.transport == "sse"
            tools_cap = SamplingCapability(tools=SamplingToolsCapability()) if self.sample else None
            async with Client(
                self.target,
                mode="legacy" if legacy else "auto",
                elicitation_callback=self.elicit,
                sampling_callback=self.sample,
                sampling_capabilities=tools_cap,
                list_roots_callback=self.roots.callback if self.roots else None,
                message_handler=self.on_message,
            ) as client:
                self.client = client
                listener = None
                if self.listen is not None and not legacy:
                    listener = asyncio.create_task(listen_tools_changed(client, self.listen))
                self.ready.set()
                await self.stop.wait()
                if listener is not None:
                    listener.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await listener
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
        self._debounce: dict[str, Debouncer] = {}
        self._calls = itertools.count(1)

    def _target(self, m: McpServerManifest) -> Any:
        env = {k: self._resolve(v) for k, v in m.env.items()}
        if m.transport == "stdio":
            return StdioServerParameters(command=m.command or "", args=m.args, env=env or None)
        if m.transport == "sse":
            return sse_target(m.url or "", {k: self._resolve(v) for k, v in m.headers.items()})
        routed = session_proxy(f"mcp:{m.name}") if m.net == "proxy" else None  # --- I9 egress proxy
        if m.headers or routed is not None:
            import httpx2
            from mcp.client.streamable_http import streamable_http_client

            headers = {k: self._resolve(v) for k, v in m.headers.items()}
            proxy = None
            if routed is not None:
                p = routed[0]
                proxy = p["server"].replace("http://", f"http://{p['username']}:{p['password']}@", 1)
            client = httpx2.AsyncClient(headers=headers, proxy=proxy, trust_env=False)
            return streamable_http_client(m.url or "", http_client=client)
        return m.url

    def _resolve(self, value: str) -> str:
        if value.startswith("secret:"):
            return self.s.secrets.get(value[7:]) or ""
        return value

    async def start(self) -> None:
        from auoreo.tools.egress import ensure

        await ensure(self.s)  # --- I9: remote `net: proxy` servers connect through it
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
        cfg = self.s.settings.i6.mcp
        debounce = self._debounce[manifest.name] = Debouncer(
            lambda: self.refresh_tools(manifest.name), cfg.refresh_debounce_s
        )
        conn.on_message = with_tools_changed(notification_handler(self.s, manifest.name), debounce)
        conn.listen = debounce if cfg.listen_tools_changed else None
        conn.roots = RootsState(manifest.roots.paths) if manifest.roots.enabled else None
        if manifest.sampling.enabled:
            conn.sample = SamplingHandler(
                self.s, manifest, lambda: conn.last_session, call_key=lambda: conn.call_id
            )
        conn.task = asyncio.create_task(conn.run(), name=f"mcp:{manifest.name}")
        self._conns[manifest.name] = conn
        try:
            await asyncio.wait_for(conn.ready.wait(), timeout)
        except TimeoutError:
            log.warning("MCP server '%s' did not start within %ss", manifest.name, timeout)
            return []
        if conn.client is None:
            return []
        names = await self._register_tools(manifest, conn.client)
        await self.sync_skills(manifest.name)
        return names

    async def refresh_tools(self, server: str) -> list[str]:
        """`tools/list_changed`: re-list, re-register (generation bump), emit an event."""
        conn = self._conns.get(server)
        if conn is None or conn.client is None:
            return []
        self.s.harness.unregister_dynamic(f"{PREFIX}{server}__")
        names = await self._register_tools(conn.manifest, conn.client)
        generation = self.s.harness.generation
        await self.s.emit(
            "server", "mcp.tools_changed", {"server": server, "tools": names, "generation": generation}
        )
        return names

    async def _register_tools(self, manifest: McpServerManifest, client: Client) -> list[str]:
        listing = await client.list_tools()
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
                net=manifest.net,
            )
            loader = self._make_loader(manifest.name, t.name, timeout_ms / 1000)
            try:
                self.s.harness.register_dynamic(ToolDef(tm, f"mcp:{manifest.name}", None, loader))
            except ToolCollisionError as exc:  # error_on_collision: skip, listed in harness errors
                log.error("MCP server '%s': %s", manifest.name, exc)
                continue
            names.append(name)
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
            conn.call_id = f"{sid}:{next(self._calls)}"
            client = conn.client
            if conn.roots is not None:
                await conn.roots.note_cwd(Path(ctx.cwd), client)
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
        if (debounce := self._debounce.pop(name, None)) is not None:
            debounce.cancel()
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
