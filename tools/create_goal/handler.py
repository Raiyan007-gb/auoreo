from auoreo.goals.tooling import attempt, fmt, goals_for
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    goals = goals_for(ctx)
    row = await attempt(goals.create(args["title"], args.get("description", ""), session_id=ctx.session_id))
    return ToolResult("created goal " + fmt(row), meta={"id": row.id})
