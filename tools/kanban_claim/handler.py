from auoreo.teams.tooling import attempt, teams_for
from auoreo.tools.context import ToolContext, ToolResult


def _fmt(row) -> str:
    worker = f" worker={row.worker}" if row.worker else ""
    return f"#{row.id} [{row.status}] {row.title} (board={row.board}{worker} failures={row.failures})"


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    kb = teams_for(ctx).kanban
    worker = args.get("worker") or ctx.session_id
    row = await attempt(kb.claim(args.get("board", "default"), worker, ctx.session_id))
    if row is None:
        return ToolResult("no ready task on this board", meta={"id": None})
    return ToolResult(f"claimed {_fmt(row)}\n{row.body}", meta={"id": row.id})
