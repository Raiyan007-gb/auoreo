"""Describe one tool the agent may use (direct or deferred): description + parameters."""

import json

from auoreo.tools.context import ToolContext, ToolError, ToolResult


def _pool(ctx: ToolContext) -> dict:
    spec = ctx.services.get("spec")
    if spec is None:
        return {}
    return {t.name: t for t in (*spec.tools, *spec.deferred)}


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    pool = _pool(ctx)
    tool = pool.get(args["name"])
    if tool is None:
        raise ToolError(f"no tool named {args['name']!r} is available to this agent; try tool_search")
    m = tool.manifest
    spec = ctx.services.get("spec")
    deferred = spec is not None and tool in spec.deferred
    how = "deferred: run it with tool_call" if deferred else "direct"
    lines = [
        f"{m.emoji + ' ' if m.emoji else ''}{m.name} ({how})",
        m.description.strip(),
        "parameters:",
        json.dumps(m.parameters, indent=2, ensure_ascii=False),
    ]
    if m.output_schema is not None:
        lines += ["output schema:", json.dumps(m.output_schema, indent=2, ensure_ascii=False)]
    flags = [f for f in ("is_read_only", "is_destructive") if getattr(m, f)]
    if flags:
        lines.append("flags: " + ", ".join(flags))
    return ToolResult("\n".join(lines), meta={"tool": m.name, "deferred": deferred})
