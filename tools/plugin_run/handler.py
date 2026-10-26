from auoreo.plugins.creator import CreatorError, approve, manager, require_opt_in, services_of
from auoreo.tools.context import ToolContext, ToolError


async def run(args: dict, ctx: ToolContext) -> str:
    try:
        require_opt_in(ctx, "plugin_run")
        mgr = manager(services_of(ctx))
        d = mgr.get(ctx.session_id, args["name"])
        if d.rejected:  # no retry-after-reject: never ask the human about this package again
            raise CreatorError(f"{d.package_id} was rejected; define a new version instead")
        if not await approve(ctx, d, mgr.preview(d)):
            await mgr.reject(d)  # tombstone: this package is never offered again
            raise ToolError(f"plugin_run {d.name} was not approved ({d.package_id} is now rejected)")
        d = await mgr.run(ctx.session_id, d.name)
    except CreatorError as exc:
        raise ToolError(str(exc)) from exc
    ctx.state.loaded_tools.add(d.tool_name)
    return f"{d.tool_name} is running (callable from the next step; each call asks for permission)"
