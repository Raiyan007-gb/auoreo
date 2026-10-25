from auoreo.plugins.creator import CreatorError, manager, require_opt_in, services_of
from auoreo.tools.context import ToolContext, ToolError


async def run(args: dict, ctx: ToolContext) -> str:
    try:
        require_opt_in(ctx, "plugin_stop")
        d = await manager(services_of(ctx)).stop(ctx.session_id, args["name"])
    except CreatorError as exc:
        raise ToolError(str(exc)) from exc
    return f"stopped {d.tool_name} (definition kept; plugin_undefine removes it)"
