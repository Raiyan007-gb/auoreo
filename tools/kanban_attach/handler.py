from auoreo.teams import kanban_guard as guard
from auoreo.teams.tooling import attempt
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    kb, _ = await guard.task_on_board(ctx, args["task_id"])
    rec = await attempt(kb.attach(args["task_id"], args["ref"], args.get("note", ""), ctx.session_id))
    return ToolResult(f"attached {rec['ref']} to #{args['task_id']}", meta=rec)
