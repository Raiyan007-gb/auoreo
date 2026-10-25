"""MCP **resources** and **prompts** served at `/mcp` (spec §25.12). Read-only.

Resources (all text, secrets redacted via `core.redaction`, ≤ `MAX_READ_BYTES` per read
with a truncation notice, every read audited as `mcp.resource.read`):

| URI | content |
|---|---|
| `auoreo://memory/<FILE>.md` | `MEMORY.md` / `USER.md` / `PREFS.md` / `PROJECT.md` under `<home>/memory/` |
| `auoreo://skills/<name>` | the skill's `SKILL.md` |
| `auoreo://harness/<kind>` | JSON of `harness.describe()[kind]` (tools, agents, skills …) |
| `auoreo://sessions/<id>` | markdown export, as `GET /api/sessions/{id}/export?format=md` |

`resources/templates/list` advertises the four parameterised forms. Sessions: the token
sees every session today, so all are readable; the listing shows the newest
`SESSION_LIST_LIMIT`.

Prompts: each `commands/<name>.md` with `handler: prompt` is a prompt with one optional
`args` argument, rendered by the same strict `{{args}}`/`{{input}}` template renderer the
runtime's slash-command dispatch uses. Engine-handled commands (`/compact`, `/model`,
`/plan`) are not exposed.

Change notifications: memory-file updates (`notifications/resources/updated`) and harness
reloads (`resources`/`prompts` list changed) are detected by a cheap mtime/generation poll
every `POLL_INTERVAL` s that runs **only while a subscriber exists**: a 2026-07-28
`subscriptions/listen` stream (SDK `ListenHandler` over an in-process bus), or a legacy
`resources/subscribe` on a handshake-era session.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import mcp.types as mt
from mcp.server.context import ServerRequestContext
from mcp.server.subscriptions import (
    InMemorySubscriptionBus,
    ListenHandler,
    PromptsListChanged,
    ResourcesListChanged,
    ResourceUpdated,
)
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS

from auoreo.core.redaction import redact
from auoreo.core.sessions_ops import export_session
from auoreo.harness.orchestrator import TemplateError, render
from auoreo.memory.files import FILES

if TYPE_CHECKING:
    from auoreo.core.services import Services

SCHEME = "auoreo://"
MAX_READ_BYTES = 256 * 1024
SESSION_LIST_LIMIT = 50
TEMPLATES = (
    (
        "memory",
        "auoreo://memory/{file}",
        "Memory file (MEMORY.md, USER.md, PREFS.md or PROJECT.md)",
        "text/markdown",
    ),
    ("skills", "auoreo://skills/{name}", "A skill's SKILL.md", "text/markdown"),
    ("harness", "auoreo://harness/{kind}", "Harness definitions of one kind (JSON)", "application/json"),
    (
        "sessions",
        "auoreo://sessions/{session_id}",
        "A session transcript (markdown, redacted)",
        "text/markdown",
    ),
)


def session_id_of(ctx: ServerRequestContext[Any]) -> str:
    headers = getattr(ctx.request, "headers", None)
    sid = headers.get("mcp-session-id") if headers is not None else None
    return str(sid) if sid else "anon"


def cap(text: str, limit: int = MAX_READ_BYTES) -> tuple[str, bool]:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text, False
    kept = raw[:limit].decode("utf-8", errors="ignore")
    return f"{kept}\n\n> [truncated by AUOREO: {limit} of {len(raw)} bytes shown (256 KiB cap)]\n", True


def _not_found(uri: str) -> MCPError:
    return MCPError(INVALID_PARAMS, f"Resource not found: {uri}", {"uri": uri})


class McpCatalog:
    """Resources + prompts + the subscription poller for one `McpEndpoint`."""

    POLL_INTERVAL = 30.0

    def __init__(self, get_services: Callable[[], Services | None]) -> None:
        self._svc = get_services
        self.bus = InMemorySubscriptionBus()
        self._listen = ListenHandler(self.bus)
        self._listeners = 0
        self._legacy: dict[str, dict[str, Any]] = {}  # uri → {Mcp-Session-Id: ServerSession}
        self._poller: asyncio.Task[None] | None = None
        self._mtimes: dict[str, float | None] = {}
        self._generation = -1

    @property
    def svc(self) -> Services:
        svc = self._svc()
        if svc is None:
            raise MCPError(INVALID_PARAMS, "MCP endpoint unavailable")
        return svc

    # ------------------------------------------------------------ listing
    async def list_resources(
        self, ctx: ServerRequestContext[Any], params: mt.PaginatedRequestParams | None
    ) -> mt.ListResourcesResult:
        svc = self.svc
        out: list[mt.Resource] = []
        mem = svc.settings.home / "memory"
        for name in FILES:
            if (mem / name).is_file():
                out.append(_res(f"{SCHEME}memory/{name}", name, "Memory file", "text/markdown"))
        for skill in sorted(svc.harness.skills.values(), key=lambda s: s.name):
            out.append(
                _res(f"{SCHEME}skills/{skill.name}", skill.name, skill.manifest.description, "text/markdown")
            )
        for kind in svc.harness.describe():
            out.append(
                _res(f"{SCHEME}harness/{kind}", f"harness:{kind}", f"Harness {kind}", "application/json")
            )
        for row in await svc.list_sessions(SESSION_LIST_LIMIT):
            out.append(
                _res(f"{SCHEME}sessions/{row.id}", row.title or row.id, "Session transcript", "text/markdown")
            )
        return mt.ListResourcesResult(resources=out)

    async def list_templates(
        self, ctx: ServerRequestContext[Any], params: mt.PaginatedRequestParams | None
    ) -> mt.ListResourceTemplatesResult:
        return mt.ListResourceTemplatesResult(
            resource_templates=[
                mt.ResourceTemplate(name=name, uri_template=tpl, description=desc, mime_type=mime)
                for name, tpl, desc, mime in TEMPLATES
            ]
        )

    # ------------------------------------------------------------ reading
    async def read_resource(
        self, ctx: ServerRequestContext[Any], params: mt.ReadResourceRequestParams
    ) -> mt.ReadResourceResult:
        svc, uri = self.svc, str(params.uri)
        kind, _, name = uri.removeprefix(SCHEME).partition("/") if uri.startswith(SCHEME) else ("", "", "")
        reader = self._readers().get(kind)
        found = reader is not None and bool(name) and "/" not in name and name not in {".", ".."}
        body: tuple[str, str] | None = await reader(name) if found and reader is not None else None
        text, truncated = cap(redact(body[0])) if body is not None else ("", False)
        await svc.audit.record(
            actor="mcp",
            action="mcp.resource.read",
            session_id=f"mcp-{session_id_of(ctx)}",
            decision="allow" if body is not None else "deny",
            reason="" if body is not None else "not found",
            detail={"uri": uri[:500], "bytes": len(text.encode()), "truncated": truncated},
        )
        if body is None:
            raise _not_found(uri)
        return mt.ReadResourceResult(
            contents=[mt.TextResourceContents(uri=uri, text=text, mime_type=body[1])]
        )

    def _readers(self) -> dict[str, Callable[[str], Any]]:
        return {
            "memory": self._read_memory,
            "skills": self._read_skill,
            "harness": self._read_harness,
            "sessions": self._read_session,
        }

    async def _read_memory(self, name: str) -> tuple[str, str] | None:
        path = self.svc.settings.home / "memory" / name
        if name not in FILES or not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="replace"), "text/markdown"

    async def _read_skill(self, name: str) -> tuple[str, str] | None:
        skill = self.svc.harness.skills.get(name)
        if skill is None or not skill.path.is_file():
            return None
        return skill.path.read_text(encoding="utf-8", errors="replace"), "text/markdown"

    async def _read_harness(self, kind: str) -> tuple[str, str] | None:
        rows = self.svc.harness.describe().get(kind)
        return None if rows is None else (json.dumps(rows, indent=2, default=str), "application/json")

    async def _read_session(self, sid: str) -> tuple[str, str] | None:
        try:
            return await export_session(self.svc, sid, "md"), "text/markdown"
        except KeyError:
            return None

    # ------------------------------------------------------------ prompts
    def _prompt_commands(self) -> dict[str, Any]:
        return {n: c for n, c in self.svc.harness.commands.items() if c.manifest.handler == "prompt"}

    async def list_prompts(
        self, ctx: ServerRequestContext[Any], params: mt.PaginatedRequestParams | None
    ) -> mt.ListPromptsResult:
        prompts = [
            mt.Prompt(
                name=name,
                description=cmd.manifest.description,
                arguments=[
                    mt.PromptArgument(
                        name="args",
                        description=cmd.manifest.args_hint or "text after the command",
                        required=False,
                    )
                ],
            )
            for name, cmd in sorted(self._prompt_commands().items())
        ]
        return mt.ListPromptsResult(prompts=prompts)

    async def get_prompt(
        self, ctx: ServerRequestContext[Any], params: mt.GetPromptRequestParams
    ) -> mt.GetPromptResult:
        cmd = self._prompt_commands().get(params.name)
        if cmd is None:
            raise MCPError(INVALID_PARAMS, f"Unknown prompt: {params.name}")
        args = str((params.arguments or {}).get("args", "")).strip()
        text = f"/{params.name} {args}".strip()
        try:  # same renderer + variables as runtime/commands.py `_render`
            rendered = render(cmd.template or "{{args}}", {"args": args, "input": text}).strip()
        except TemplateError as exc:
            raise MCPError(INVALID_PARAMS, str(exc)) from None
        message = mt.PromptMessage(role="user", content=mt.TextContent(text=rendered or text))
        return mt.GetPromptResult(description=cmd.manifest.description, messages=[message])

    # ------------------------------------------------------- subscriptions
    async def listen(
        self, ctx: ServerRequestContext[Any], params: mt.SubscriptionsListenRequestParams
    ) -> mt.SubscriptionsListenResult:
        """`subscriptions/listen` (2026-07-28): the SDK handler, with the poller running
        for as long as the stream is open."""
        self._listeners += 1
        self._ensure_poller()
        try:
            return await self._listen(ctx, params)
        finally:
            self._listeners -= 1

    async def subscribe(
        self, ctx: ServerRequestContext[Any], params: mt.SubscribeRequestParams
    ) -> mt.EmptyResult:
        self._legacy.setdefault(str(params.uri), {})[session_id_of(ctx)] = ctx.session
        self._ensure_poller()
        return mt.EmptyResult()

    async def unsubscribe(
        self, ctx: ServerRequestContext[Any], params: mt.UnsubscribeRequestParams
    ) -> mt.EmptyResult:
        uri = str(params.uri)
        self._legacy.get(uri, {}).pop(session_id_of(ctx), None)
        if not self._legacy.get(uri):
            self._legacy.pop(uri, None)
        return mt.EmptyResult()

    @property
    def has_subscribers(self) -> bool:
        return self._listeners > 0 or bool(self._legacy)

    def _ensure_poller(self) -> None:
        if self._poller is None or self._poller.done():
            self._snapshot()
            self._poller = asyncio.create_task(self._poll(), name="mcp-resource-poll")

    def _snapshot(self) -> list[str]:
        """Record memory mtimes; returns the URIs whose mtime changed since last time."""
        changed: list[str] = []
        svc = self._svc()
        if svc is None:
            return changed
        for name in FILES:
            path = svc.settings.home / "memory" / name
            with contextlib.suppress(OSError):
                mtime: float | None = path.stat().st_mtime_ns if path.exists() else None
                uri = f"{SCHEME}memory/{name}"
                if uri in self._mtimes and self._mtimes[uri] != mtime:
                    changed.append(uri)
                self._mtimes[uri] = mtime
        return changed

    async def _poll(self) -> None:
        svc = self._svc()
        self._generation = svc.harness.generation if svc is not None else -1
        while self.has_subscribers:
            await asyncio.sleep(self.POLL_INTERVAL)
            try:
                await self.tick()
            except Exception:  # a notification failure must never kill the poller
                logging.getLogger(__name__).debug("mcp resource poll failed", exc_info=True)

    async def tick(self) -> None:
        """One poll: publish memory-file updates and harness list changes."""
        for uri in self._snapshot():
            await self.bus.publish(ResourceUpdated(uri=uri))
            subscribers = self._legacy.get(uri, {})
            for sid, session in list(subscribers.items()):
                try:
                    await session.send_resource_updated(uri)
                except Exception:  # closed session: forget it
                    subscribers.pop(sid, None)
            if not self._legacy.get(uri):
                self._legacy.pop(uri, None)
        svc = self._svc()
        if svc is not None and svc.harness.generation != self._generation:
            self._generation = svc.harness.generation
            await self.bus.publish(ResourcesListChanged())
            await self.bus.publish(PromptsListChanged())

    async def close(self) -> None:
        self._legacy.clear()
        if self._poller is not None:
            self._poller.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._poller
            self._poller = None


def _res(uri: str, name: str, description: str, mime: str) -> mt.Resource:
    return mt.Resource(uri=uri, name=name, description=description, mime_type=mime)
