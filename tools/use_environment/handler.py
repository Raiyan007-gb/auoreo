"""Set the session's execution environment (`SessionState.environment`).

The switch is refused when the environment's backend `check()` fails, so the model learns
about a missing key or an unreachable host here rather than on the next command.
"""

from typing import Any

from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.environments import pool_for


def _listing(ctx: ToolContext, envs: dict[str, Any]) -> str:
    current = ctx.state.environment or "default"
    lines = [f"current: {current}", "- default: settings.backends.environment (local unless set)"]
    for name, entry in sorted(envs.items()):
        desc = f" — {entry.description}" if entry.description else ""
        lines.append(f"- {name}: backend {entry.backend}{desc}")
    return "\n".join(lines)


async def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    svc = ctx.services.get("services")
    if svc is None:
        raise ToolError("no services attached; environments unavailable")
    envs = svc.settings.environments
    name = str(args.get("name", "")).strip()
    if not name:
        return ToolResult(_listing(ctx, envs))
    if name == "default":
        ctx.state.environment = None
        return ToolResult("environment: default (settings.backends.environment)", meta={"environment": None})
    if name not in envs:
        known = ", ".join(sorted(envs)) or "none configured"
        raise ToolError(f"unknown environment '{name}' (settings.environments: {known})")
    backend = pool_for(svc).instance(name)
    status = await backend.check()
    if not status.ok:
        fix = f" — {status.remedy}" if status.remedy else ""
        raise ToolError(f"environment '{name}' is not ready: {status.detail}{fix}")
    ctx.state.environment = name
    where = "remote" if getattr(backend, "remote", False) else "local"
    return ToolResult(
        f"environment: {name} (backend {envs[name].backend}, {where}) — {status.detail}",
        meta={"environment": name, "backend": envs[name].backend, "remote": where == "remote"},
    )
