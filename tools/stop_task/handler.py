"""stop_task (spec §16.5 coordinator allowlist): stop one of the caller's own subagents."""

from auoreo.teams.tooling import attempt, teams_for
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    teams = teams_for(ctx)
    out = await attempt(teams.control.stop(ctx.session_id, args["task_id"]))
    if args.get("reason"):
        await teams.s.emit(
            ctx.session_id,
            "subagent.stop_requested",
            {"parent": ctx.session_id, "child": out["subagent_id"], "reason": str(args["reason"])[:1000]},
        )
    text = f"stopped {out['name']}" if out["stopped"] else f"{out['name']} was not running"
    return ToolResult(text, meta=out)
