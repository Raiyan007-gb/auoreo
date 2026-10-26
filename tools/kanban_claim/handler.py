from auoreo.teams import kanban_guard as guard
from auoreo.teams.tooling import attempt, teams_for
from auoreo.tools.context import ToolContext, ToolError, ToolResult


def _fmt(row) -> str:
    worker = f" worker={row.worker}" if row.worker else ""
    return f"#{row.id} [{row.status}] {row.title} (board={row.board}{worker} failures={row.failures})"


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    kb = teams_for(ctx).kanban
    pinned = kb.worker_names.get(ctx.session_id)  # a dispatched worker claims as itself
    if pinned and args.get("worker") not in (None, pinned):
        raise ToolError(f"kanban workers claim as themselves ({pinned})")
    worker = pinned or args.get("worker") or ctx.session_id
    row = await attempt(kb.claim(guard.board_arg(ctx, args), worker, ctx.session_id))
    if row is None:
        return ToolResult("no ready task on this board", meta={"id": None})
    return ToolResult(f"claimed {_fmt(row)}\n{row.body}", meta={"id": row.id})
