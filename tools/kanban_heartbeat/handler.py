from auoreo.teams import kanban_guard as guard
from auoreo.teams.tooling import attempt
from auoreo.tools.context import ToolContext, ToolResult


def _fmt(row) -> str:
    worker = f" worker={row.worker}" if row.worker else ""
    return f"#{row.id} [{row.status}] {row.title} (board={row.board}{worker} failures={row.failures})"


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    kb, _ = await guard.task_on_board(ctx, args["task_id"])
    row = await attempt(kb.heartbeat(args["task_id"], args.get("worker")))
    return ToolResult("heartbeat " + _fmt(row), meta={"id": row.id})
