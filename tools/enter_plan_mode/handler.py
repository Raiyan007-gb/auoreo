from auoreo.tools.context import ToolContext


async def run(args: dict, ctx: ToolContext) -> str:
    engine = ctx.services.get("engine")
    if engine is not None:
        ctx.services["previous_mode"] = engine.mode
        engine.mode = "plan"
    ctx.state.plan_mode = True
    await ctx.emit("plan.mode", {"active": True})
    return (
        "Plan mode is on. Edits are blocked except under .auoreo/plans/. "
        "Research, then present the plan and call exit_plan_mode for approval."
    )
