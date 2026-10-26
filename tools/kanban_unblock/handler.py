"""kanban_unblock: orchestrator-only (spec §16.6) — leaf subagents are refused."""

from auoreo.teams import kanban_guard as guard
from auoreo.teams.tooling import attempt, teams_for
from auoreo.tools.context import ToolContext, ToolError, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    guard.refuse_worker(ctx, "unblock")
    teams = teams_for(ctx)
    if teams.roles.get(ctx.session_id) == "leaf":
        raise ToolError("kanban_unblock is for orchestrators; report the problem to your parent instead")
    row = await attempt(teams.kanban.unblock(args["task_id"], ctx.session_id, args.get("note", "")))
    return ToolResult(
        f"#{row.id} [{row.status}] {row.title} unblocked", meta={"id": row.id, "status": row.status}
    )
