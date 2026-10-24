from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    answer = await ctx.question(
        [
            {
                "question": "Approve this plan and start implementing?",
                "header": "Plan",
                "plan": args["plan"],
                "options": [{"label": "Approve"}, {"label": "Keep planning"}],
            }
        ]
    )
    picked = (answer.get("responses") or [{}])[0].get("answers", [])
    if answer.get("timed_out") or "Approve" not in picked:
        feedback = (answer.get("responses") or [{}])[0].get("customText", "")
        return ToolResult(f"Plan not approved. {feedback}".strip(), is_error=True)
    engine = ctx.services.get("engine")
    if engine is not None:
        engine.mode = ctx.services.pop("previous_mode", "manual")
    ctx.state.plan_mode = False
    await ctx.emit("plan.mode", {"active": False})
    return ToolResult("Plan approved; plan mode is off. Implement it now.")
