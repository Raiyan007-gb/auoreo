from auoreo.tools.context import ToolContext, ToolResult
from auoreo.tools.testrun import plan_hash

NOT_VERIFIED = (
    "Plan mode stays on: settings.plan_requires_verify is set and verify_plan has not passed for "
    "the current plan. Record the steps with update_plan (each with done_when), call verify_plan "
    "until it passes, then call exit_plan_mode again."
)


def _verified(ctx: ToolContext) -> bool:
    settings = getattr(ctx.services.get("services"), "settings", None)
    if not getattr(settings, "plan_requires_verify", False):
        return True
    plan = list(ctx.state.plan)
    return bool(plan) and ctx.state.plan_verified == plan_hash(plan)


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    if not _verified(ctx):
        return ToolResult(NOT_VERIFIED, is_error=True)
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
