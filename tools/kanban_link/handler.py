from auoreo.teams import kanban_guard as guard
from auoreo.teams.tooling import attempt
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    kb, _ = await guard.task_on_board(ctx, args["task_id"])
    await guard.task_on_board(ctx, args["other_task_id"])
    rec = await attempt(kb.link(args["task_id"], args["kind"], args["other_task_id"], ctx.session_id))
    return ToolResult(f"#{args['task_id']} {rec['kind']} #{rec['task_id']}", meta=rec)
