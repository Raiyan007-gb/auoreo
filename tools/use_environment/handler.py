"""Set the session's execution environment (`SessionState.environment`).

The switch is refused when the environment's backend `check()` fails, so the model learns
about a missing key or an unreachable host here rather than on the next command. Names come
from `settings.environments` and, with `settings.m7.ssh.hosts` on, from `hosts/<name>.yaml`;
an SSH environment then passes the per-host `ssh` gate before anything connects.
"""

from typing import Any

from auoreo.core import hosts
from auoreo.tools import environments
from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.environments import pool_for


def _listing(ctx: ToolContext, svc: Any) -> str:
    current = ctx.state.environment or "default"
    lines = [f"current: {current}", "- default: settings.backends.environment (local unless set)"]
    for name in hosts.environment_names(svc):
        env = hosts.resolve_environment(svc, name)
        if env is not None:
            desc = f" — {env.description}" if env.description else ""
            lines.append(f"- {name}: backend {env.backend}{desc}")
    return "\n".join(lines)


async def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    svc = ctx.services.get("services")
    if svc is None:
        raise ToolError("no services attached; environments unavailable")
    name = str(args.get("name", "")).strip()
    why = args.get("justification")
    if not name:
        return ToolResult(_listing(ctx, svc))
    if name == "default":
        ctx.state.environment = None
        if hosts.hosts_enabled(svc.settings):  # the default backend may itself be `ssh`
            label, backend = environments.resolve(ctx)
            await environments.gate(ctx, label, backend, tool="use_environment", why=why)
        return ToolResult("environment: default (settings.backends.environment)", meta={"environment": None})
    env = hosts.resolve_environment(svc, name)
    if env is None:
        raise ToolError(hosts.unknown_message(svc, name, none="none configured"))
    backend = pool_for(svc).instance(name)
    await environments.gate(ctx, name, backend, tool="use_environment", why=why)
    status = await backend.check()
    if not status.ok:
        fix = f" — {status.remedy}" if status.remedy else ""
        raise ToolError(f"environment '{name}' is not ready: {status.detail}{fix}")
    ctx.state.environment = name
    where = "remote" if getattr(backend, "remote", False) else "local"
    return ToolResult(
        f"environment: {name} (backend {env.backend}, {where}) — {status.detail}",
        meta={"environment": name, "backend": env.backend, "remote": where == "remote"},
    )
