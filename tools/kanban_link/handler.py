from auoreo.teams.tooling import attempt, teams_for
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    kb = teams_for(ctx).kanban
    rec = await attempt(kb.link(args["task_id"], args["kind"], args["other_task_id"], ctx.session_id))
    return ToolResult(f"#{args['task_id']} {rec['kind']} #{rec['task_id']}", meta=rec)
