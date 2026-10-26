from auoreo.teams.tooling import attempt, teams_for
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    kb = teams_for(ctx).kanban
    rec = await attempt(kb.attach(args["task_id"], args["ref"], args.get("note", ""), ctx.session_id))
    return ToolResult(f"attached {rec['ref']} to #{args['task_id']}", meta=rec)
