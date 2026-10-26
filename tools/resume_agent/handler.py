"""resume_agent (spec §16.4): continue a finished child with a new prompt. The child keeps its
session (history, role, permission ceiling); the run obeys the pause switch and budgets."""

from auoreo.teams.tooling import attempt, teams_for
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    teams = teams_for(ctx)
    background = bool(args.get("background", False))
    m = await attempt(teams.control.resume(ctx.session_id, args["task_id"], args["prompt"], background))
    meta = {"task_id": m.task_id, "name": m.name, "agent": m.agent, "status": m.status}
    if background:
        return ToolResult(
            f"Resumed '{m.name}' in the background (task-id {m.task_id}); the result arrives as a "
            f"task-notification, or call wait_agent(name='{m.name}').",
            meta=meta,
        )
    return ToolResult(m.notification(), is_error=m.status != "completed", meta={**meta, "usage": m.usage})
