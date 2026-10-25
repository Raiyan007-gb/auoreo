from auoreo.plugins.creator import CreatorError, manager, require_opt_in, services_of
from auoreo.tools.context import ToolContext, ToolError


async def run(args: dict, ctx: ToolContext) -> str:
    try:
        require_opt_in(ctx, "plugin_define")
        mgr = manager(services_of(ctx))
        d = await mgr.define(
            ctx.session_id, args["name"], args["description"], args.get("parameters"), args["code"]
        )
    except CreatorError as exc:
        raise ToolError(str(exc)) from exc
    return (
        f"defined {d.name} (sha256 {d.sha[:12]}); not active yet: call plugin_run "
        f'{{"name": "{d.name}"}} to start it as {d.tool_name}'
    )
