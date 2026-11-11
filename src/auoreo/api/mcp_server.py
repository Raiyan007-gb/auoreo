"""`POST /mcp` — AUOREO as an MCP **server** (streamable HTTP, spec §5.4/§25.12).

External MCP clients get the `direct` tools of the configured agent (`settings.agent`,
`default` unless changed). Tools are schema-first (a JSON Schema per folder), so this uses
the SDK's low-level `Server(on_list_tools=…, on_call_tool=…)` rather than `MCPServer`'s
typed decorators: the listing is rebuilt from the live harness on every request, so a hot
reload is visible without restarting. The SDK's `StreamableHTTPSessionManager` owns the
transport: `Mcp-Session-Id` issue/validation (400/404), `DELETE`, 202 for notifications
and the optional GET SSE stream.

Every call goes through `ToolExecutor.run` with a `PermissionEngine` built from the
agent's rules and the unbypassable floors, so permissions, hooks, redaction, pruning and
the audit log apply exactly as for the agent. Calls are **non-interactive**: anything
whose permission resolves to `ask` is denied (add an allow rule to permit it).

Resources (`auoreo://memory|skills|harness|sessions/…`) and prompts (slash commands with
`handler: prompt`) live in `api/mcp_resources.py`; capabilities advertise `resources`
(with `subscribe`) and `prompts`, plus `listChanged` on 2026-07-28 sessions that open a
`subscriptions/listen` stream.

Auth runs in front of the SDK: the Bearer token (or `auoreo_token` cookie) when a server
password is set, and a browser `Origin` must be loopback or in `cors_origins` (403).
"""

from __future__ import annotations

import json
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import mcp.types as mt
from mcp.server import Server
from mcp.server.context import ServerRequestContext
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

from auoreo import __version__
from auoreo.api.mcp_resources import McpCatalog, session_id_of
from auoreo.api.session_auth import COOKIE, client_ip, principal
from auoreo.api.ws import origin_allowed
from auoreo.core.permissions import PermissionEngine
from auoreo.core.policy import policy_engine
from auoreo.gateway.resolve import resolve_model
from auoreo.gateway.types import ToolCall
from auoreo.harness.loader import ToolDef
from auoreo.harness.orchestrator import AgentSpec, build_spec, floors
from auoreo.memory.recall import memory_for
from auoreo.tools.context import SessionState, ToolContext

if TYPE_CHECKING:
    from auoreo.core.services import Services
    from auoreo.core.settings import Settings

NON_INTERACTIVE_NOTE = (
    " [Served over MCP non-interactively: a call that needs approval is denied; "
    "allow it with a permission rule.]"
)
MAX_SESSIONS_STATE = 256
"""Per-MCP-session tool state (e.g. read-before-edit tracking) kept for this many sessions."""


class McpEndpoint:
    """ASGI app served at `/mcp`. Bound to `Services` by `lifespan()` (run by the host app,
    because a mounted sub-app's own lifespan never runs)."""

    def __init__(self) -> None:
        self.catalog = McpCatalog(lambda: self.svc)
        cat = self.catalog
        self.server: Server[Any] = Server(
            "auoreo",
            version=__version__,
            instructions=(
                "AUOREO harness tools, read-only resources (auoreo://memory, skills, harness, "
                "sessions) and slash-command prompts. Tool calls run non-interactively under "
                "AUOREO permissions."
            ),
            on_list_tools=self._list_tools,
            on_call_tool=self._call_tool,
            on_list_resources=cat.list_resources,
            on_list_resource_templates=cat.list_templates,
            on_read_resource=cat.read_resource,
            on_subscribe_resource=cat.subscribe,
            on_unsubscribe_resource=cat.unsubscribe,
            on_subscriptions_listen=cat.listen,
            on_list_prompts=cat.list_prompts,
            on_get_prompt=cat.get_prompt,
        )
        self.manager = StreamableHTTPSessionManager(app=self.server)
        self.svc: Services | None = None
        self._states: OrderedDict[str, SessionState] = OrderedDict()

    @asynccontextmanager
    async def lifespan(self, svc: Services) -> AsyncIterator[None]:
        self.svc = svc
        try:
            async with self.manager.run():
                yield
        finally:
            await self.catalog.close()
            self.svc = None

    # ------------------------------------------------------------------ ASGI
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        svc = self.svc
        if scope["type"] != "http" or svc is None:
            await JSONResponse({"detail": "MCP endpoint unavailable"}, 503)(scope, receive, send)
            return
        request = Request(scope)
        if not origin_allowed(request.headers.get("origin"), svc.settings.cors_origins):
            await JSONResponse({"detail": "origin not allowed"}, 403)(scope, receive, send)
            return
        if (
            principal(
                svc,
                authorization=request.headers.get("authorization"),
                cookie=request.cookies.get(COOKIE),
                ip=client_ip(request),
            )
            is None
        ):
            await JSONResponse(
                {"detail": "invalid or missing token"}, 401, headers={"WWW-Authenticate": "Bearer"}
            )(scope, receive, send)
            return
        version = request.headers.get("mcp-protocol-version")
        await self.manager.handle_request(scope, receive, _echo_version(send, version))

    # ----------------------------------------------------------------- tools
    def _prepare(self) -> tuple[Services, AgentSpec, dict[str, ToolDef], PermissionEngine]:
        svc = self.svc
        assert svc is not None
        agent = svc.harness.agent(svc.settings.agent)
        mode = agent.manifest.permission_mode or svc.settings.permission_mode
        model = resolve_model(svc.settings, agent_model=agent.manifest.model)
        spec = build_spec(svc.harness, agent.name, model, mode)
        tools = {t.name: t for t in spec.tools if not t.manifest.interactive_only}
        plans = str(svc.cwd / ".auoreo" / "plans")
        engine = PermissionEngine(
            list(spec.rules),
            floors(svc.harness),
            mode,
            plans,
            policies=policy_engine(svc.harness),
            context={"agent": spec.name, "cwd": str(svc.cwd)},
        )
        return svc, spec, tools, engine

    async def _list_tools(
        self, ctx: ServerRequestContext[Any], params: mt.PaginatedRequestParams | None
    ) -> mt.ListToolsResult:
        _, _, tools, _ = self._prepare()
        return mt.ListToolsResult(
            tools=[
                mt.Tool(
                    name=t.name,
                    description=t.manifest.description + NON_INTERACTIVE_NOTE,
                    input_schema=t.manifest.parameters or {"type": "object"},
                )
                for t in tools.values()
            ]
        )

    async def _call_tool(
        self, ctx: ServerRequestContext[Any], params: mt.CallToolRequestParams
    ) -> mt.CallToolResult:
        svc, spec, tools, engine = self._prepare()
        sid = f"mcp-{_session_id(ctx)}"  # a fresh id per call when the transport has none
        # the agent's memory tenant, as the runner resolves it (None = fail closed, no memory)
        memory = await memory_for(svc, spec.agent.manifest)
        tctx = ToolContext(
            session_id=sid,
            cwd=svc.cwd,
            home=svc.settings.home,
            harness=svc.harness,
            state=self._state(sid),
            interactive=False,
            services={"services": svc, "spec": spec, "engine": engine, "memory": memory},
        )
        call = ToolCall(
            id=f"mcp_{uuid.uuid4().hex[:12]}", name=params.name, arguments=json.dumps(params.arguments or {})
        )
        outcome = await svc.coordinator.runner.executor.run(call, tools=tools, ctx=tctx, engine=engine)
        result = outcome.result
        content: list[mt.ContentBlock] = [mt.TextContent(text=result.content)]
        content += [mt.ImageContent(data=i.data_b64, mime_type=i.media_type) for i in result.images]
        return mt.CallToolResult(content=content, is_error=result.is_error)

    def _state(self, sid: str) -> SessionState:
        state = self._states.pop(sid, None) or SessionState()
        self._states[sid] = state
        while len(self._states) > MAX_SESSIONS_STATE:
            self._states.popitem(last=False)
        return state


def _session_id(ctx: ServerRequestContext[Any]) -> str:
    sid = session_id_of(ctx)
    return uuid.uuid4().hex if sid == "anon" else sid


def _echo_version(send: Send, version: str | None) -> Callable[[Message], Awaitable[None]]:
    """Echo the client's `MCP-Protocol-Version` on the response (unless the SDK set it)."""

    async def wrapped(message: Message) -> None:
        if version and message["type"] == "http.response.start":
            headers = list(message.get("headers", []))
            if not any(k.lower() == b"mcp-protocol-version" for k, _ in headers):
                headers.append((b"mcp-protocol-version", version.encode("latin-1")))
            message = {**message, "headers": headers}
        await send(message)

    return wrapped


def make_mount(settings: Settings) -> McpEndpoint | None:
    """`MOUNTS` factory for `api/app.py`: one endpoint per app (a session manager runs once)."""
    return McpEndpoint() if settings.mcp_server_enabled else None
