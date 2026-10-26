import json

from auoreo.plugins.creator import CreatorError, manager, require_opt_in, services_of
from auoreo.tools.context import ToolContext, ToolError


async def run(args: dict, ctx: ToolContext) -> str:
    try:
        require_opt_in(ctx, "plugin_inspect")
        mgr = manager(services_of(ctx))
        mgr.mark_inspected(ctx.session_id)  # inspect before define (spec §15)
    except CreatorError as exc:
        raise ToolError(str(exc)) from exc
    defs = mgr.list(ctx.session_id, args.get("query"))
    if not defs:
        return "no creator tools defined in this session"
    return json.dumps([mgr.describe(d) for d in defs], indent=2)
