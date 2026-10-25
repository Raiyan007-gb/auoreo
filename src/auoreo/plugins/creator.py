"""Creator mode (spec §15): a session defines a tool from Python source at runtime.

`define` writes `async def run(args, ctx)` to `<home>/sessions/<id>/creator/<name>.py` and
does nothing else; `run` (approval-gated by the calling tool) spawns a sandbox process — the
base interpreter with `-I`, a scrubbed env and the creator folder as cwd, running
`auoreo_plugin_rt --ephemeral` — and registers `creator__<session8>__<name>` as a dynamic
tool (default action `ask`, callable only from the defining session). Nothing survives a
restart: definitions live in memory and the folder is removed on close. Every operation is
audited with `{session_id, plugin, package_hash}`.
"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from auoreo.core.secrets import child_env
from auoreo.harness.loader import ToolDef
from auoreo.harness.manifests import ToolManifest
from auoreo.plugins.bridge import make_tool
from auoreo.plugins.install import RT_FILE
from auoreo.plugins.protocol import RpcError, RpcPeer
from auoreo.tools.context import ToolContext, ToolResult
from auoreo.tools.context import _no_ask as _default_ask

if TYPE_CHECKING:
    from auoreo.core.services import Services

NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
EXT_KEY = "creator"


class CreatorError(ValueError):
    pass


@dataclass
class Definition:
    session_id: str
    name: str
    description: str
    parameters: dict[str, Any]
    code: str
    path: Path
    tool_name: str
    sha: str = ""
    peer: RpcPeer | None = field(default=None, repr=False)
    tool: ToolDef | None = field(default=None, repr=False)

    @property
    def active(self) -> bool:
        return self.peer is not None and self.peer.alive


def tool_name_for(session_id: str, name: str) -> str:
    short = re.sub(r"[^a-z0-9]", "", session_id.removeprefix("ses_").lower())[:8] or "x"
    return f"creator__{short}__{name}"


def _check_code(code: str) -> None:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise CreatorError(f"syntax error at line {exc.lineno}: {exc.msg}") from exc
    if not any(isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef) and n.name == "run" for n in tree.body):
        raise CreatorError("code must define `async def run(args, ctx)` at module level")


class CreatorManager:
    def __init__(self, services: Services) -> None:
        self.s = services
        self.defs: dict[tuple[str, str], Definition] = {}

    def folder(self, session_id: str) -> Path:
        return self.s.settings.home / "sessions" / session_id / "creator"

    async def audit(self, action: str, d: Definition, decision: str = "allow", reason: str = "") -> None:
        await self.s.audit.record(
            actor="creator",
            action=f"creator.{action}",
            session_id=d.session_id,
            decision=decision,
            reason=reason[:64],
            detail={"session_id": d.session_id, "plugin": d.name, "package_hash": d.sha},
        )

    def get(self, session_id: str, name: str) -> Definition:
        d = self.defs.get((session_id, name))
        if d is None:
            raise CreatorError(f"no definition '{name}' in this session (see plugin_inspect)")
        return d

    def list(self, session_id: str, query: str | None = None) -> list[Definition]:
        out = [d for (sid, _), d in sorted(self.defs.items()) if sid == session_id]
        if query:
            q = query.lower()
            out = [d for d in out if q in d.name.lower() or q in d.description.lower()]
        return out

    async def define(
        self, session_id: str, name: str, description: str, parameters: dict[str, Any] | None, code: str
    ) -> Definition:
        if not NAME.fullmatch(name):
            raise CreatorError("name must match ^[a-z][a-z0-9_]{0,31}$")
        params = parameters or {"type": "object", "properties": {}}
        try:
            ToolManifest(name="probe", description="probe", parameters=params)
        except ValueError as exc:
            raise CreatorError(f"bad parameters schema: {exc}") from exc
        _check_code(code)
        if (session_id, name) in self.defs:
            await self.stop(session_id, name)
        folder = self.folder(session_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}.py"
        path.write_text(code, encoding="utf-8")
        d = Definition(
            session_id,
            name,
            description or name,
            params,
            code,
            path,
            tool_name_for(session_id, name),
            hashlib.sha256(code.encode()).hexdigest(),
        )
        self.defs[(session_id, name)] = d
        await self.audit("define", d)
        return d

    def preview(self, d: Definition, limit: int = 1500) -> str:
        head = f"creator tool {d.tool_name} (sha256 {d.sha[:12]})\n{d.description}\n---\n"
        return head + (d.code if len(d.code) <= limit else d.code[:limit] + "\n…")

    async def run(self, session_id: str, name: str) -> Definition:
        """Spawn the sandbox and register the tool. The caller has obtained approval."""
        d = self.get(session_id, name)
        if d.active:
            return d
        argv = [getattr(sys, "_base_executable", sys.executable), "-I", str(RT_FILE)]
        argv += ["--ephemeral", str(d.path), "--name", d.name]

        async def on_request(method: str, params: dict[str, Any]) -> Any:
            raise RpcError("creator sandboxes cannot call the host", code=-32001)

        async def on_notify(method: str, params: dict[str, Any]) -> None:
            return None

        env = {**child_env([], None), "AUOREO_PLUGIN": f"creator:{d.name}"}
        peer = RpcPeer(
            d.tool_name, argv, env=env, cwd=d.path.parent, on_request=on_request, on_notify=on_notify
        )
        await peer.start()
        try:
            info = await peer.request(
                "initialize",
                {
                    "config": {"description": d.description, "parameters": d.parameters},
                    "consumes_granted": [],
                },
                timeout=20,
            )
        except RpcError as exc:
            await peer.close()
            await self.audit("run", d, "error", str(exc))
            raise CreatorError(
                f"sandbox failed to start: {exc}\n{chr(10).join(peer.stderr_tail[-5:])}"
            ) from exc
        d.peer = peer
        entry = (info.get("tools") or [{}])[0] if isinstance(info, dict) else {}
        entry = {**entry, "name": d.name, "description": d.description, "parameters": d.parameters}
        tool = make_tool(
            d.name,
            entry,
            self._caller(d),
            default_timeout_s=self.s.settings.plugins_tool_timeout_s,
            toolset=f"creator:{session_id}",
            name=d.tool_name,
        )
        d.tool = tool
        self.s.harness.register_dynamic(tool)
        await self.audit("run", d)
        return d

    def _caller(self, d: Definition) -> Any:
        async def call(remote: str, args: dict[str, Any], session_id: str, timeout: float) -> ToolResult:
            if session_id != d.session_id:
                return ToolResult(f"{d.tool_name} belongs to another session", is_error=True)
            if d.peer is None or not d.peer.alive:
                return ToolResult(f"{d.tool_name} is not running (plugin_run it again)", is_error=True)
            try:
                res = await d.peer.request(
                    "tool.call", {"name": remote, "args": args, "session_id": session_id}, timeout=timeout
                )
            except RpcError as exc:
                return ToolResult(f"{d.tool_name} failed: {exc}", is_error=True)
            res = res if isinstance(res, dict) else {}
            return ToolResult(str(res.get("content", "")), is_error=bool(res.get("is_error")))

        return call

    async def stop(self, session_id: str, name: str) -> Definition:
        d = self.get(session_id, name)
        self._unregister(d)
        if d.peer is not None:
            await d.peer.close()
            d.peer = None
        await self.audit("stop", d)
        return d

    def _unregister(self, d: Definition) -> None:
        """`unregister_dynamic` is prefix-based: put back siblings that share the prefix."""
        self.s.harness.unregister_dynamic(d.tool_name)
        d.tool = None
        for other in self.defs.values():
            if other.tool is not None and other.tool_name.startswith(d.tool_name):
                self.s.harness.register_dynamic(other.tool)

    async def undefine(self, session_id: str, name: str) -> Definition:
        d = await self.stop(session_id, name)
        del self.defs[(session_id, name)]
        d.path.unlink(missing_ok=True)
        await self.audit("undefine", d)
        return d

    def describe(self, d: Definition) -> dict[str, Any]:
        return {
            "name": d.name,
            "tool": d.tool_name,
            "active": d.active,
            "description": d.description,
            "package_hash": d.sha,
            "parameters": json.dumps(d.parameters)[:300],
        }

    async def close(self) -> None:
        for d in list(self.defs.values()):
            self.s.harness.unregister_dynamic(d.tool_name)
            if d.peer is not None:
                await d.peer.close()
            shutil.rmtree(d.path.parent, ignore_errors=True)
        self.defs.clear()


def manager(services: Services) -> CreatorManager:
    """The process-wide manager (created on first use, closed with the extensions)."""
    mgr = services.ext.get(EXT_KEY)
    if mgr is None:
        mgr = services.ext[EXT_KEY] = CreatorManager(services)
    return mgr


def services_of(ctx: ToolContext) -> Services:
    svc = ctx.services.get("services")
    if svc is None:
        raise CreatorError("creator mode needs the server services (not available here)")
    return svc  # type: ignore[no-any-return]


def require_opt_in(ctx: ToolContext, tool: str) -> None:
    """Creator tools work only for agents that name them explicitly (not via a bare `*`),
    so the bundled `default` agent does not get creator mode by accident."""
    spec = ctx.services.get("spec")
    if spec is None:
        return
    allow = [g for g in spec.agent.manifest.tools.allow if g != "*"]
    if not any(fnmatch.fnmatchcase(tool, g) for g in allow):
        raise CreatorError(
            f"{tool} is creator-mode only: use the `creator` agent or list `{tool}` "
            "in the agent's tools.allow"
        )


async def approve(ctx: ToolContext, d: Definition, preview: str) -> bool:
    """Human approval for `plugin_run`: `ctx.ask` when the host wired one, otherwise the
    permission engine (`plugin_run` rules) and the approval broker with a code preview."""
    if ctx.ask is not _default_ask:
        return await ctx.ask("plugin_run", d.name, preview)
    engine = ctx.services.get("engine")
    svc = ctx.services.get("services")
    if engine is not None:
        decision = engine.evaluate("plugin_run", d.name, "ask")
        if decision.action != "ask":
            return bool(decision.action == "allow")
    if not ctx.interactive or svc is None:
        return False
    outcome = await svc.approvals.request(ctx.session_id, "plugin_run", "plugin_run", d.name, preview)
    if outcome.granted and outcome.reply == "always" and engine is not None:
        engine.grant_always("plugin_run", d.name)
    return bool(outcome.granted)
