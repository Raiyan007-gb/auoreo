"""Run a deferred tool through the normal executor (validation, hooks, permission, approval)."""

import json

from auoreo.gateway.types import ToolCall
from auoreo.tools.context import CURRENT_CALL, ToolContext, ToolError, ToolResult

META_TOOLS = frozenset({"tool_call", "tool_describe", "tool_search"})


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    name = args["name"]
    spec = ctx.services.get("spec")
    executor = ctx.services.get("executor")
    engine = ctx.services.get("engine")
    if spec is None or executor is None or engine is None:
        raise ToolError("tool_call needs a running agent turn")
    if name in META_TOOLS:
        raise ToolError(f"'{name}' cannot be called through tool_call")
    pool = {t.name: t for t in spec.deferred}
    if name not in pool:
        if any(t.name == name for t in spec.tools):
            raise ToolError(f"'{name}' is a direct tool: call it directly")
        raise ToolError(f"no deferred tool named {name!r} is available to this agent; try tool_search")
    current = CURRENT_CALL.get()
    inner_id = f"{current.call_id if current else 'call'}.{name}"
    call = ToolCall(inner_id, name, json.dumps(args.get("arguments") or {}, ensure_ascii=False))
    outcome = await executor.run(call, tools=pool, ctx=ctx, engine=engine)
    r = outcome.result
    meta = {**r.meta, "inner_tool": name, **({"halted": True} if outcome.halt else {})}
    return ToolResult(r.content, r.is_error, meta, images=r.images)
