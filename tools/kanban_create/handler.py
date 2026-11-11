from auoreo.teams import kanban_guard as guard
from auoreo.teams.tooling import attempt, teams_for
from auoreo.tools.context import ToolContext, ToolResult


def _fmt(row) -> str:
    worker = f" worker={row.worker}" if row.worker else ""
    return f"#{row.id} [{row.status}] {row.title} (board={row.board}{worker} failures={row.failures})"


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    kb = teams_for(ctx).kanban
    await guard.refuse_unattended(ctx)
    board = guard.board_arg(ctx, args)
    row = await attempt(kb.create(board, args["title"], args.get("body", ""), profile=args.get("profile")))
    return ToolResult("created " + _fmt(row), meta={"id": row.id})
