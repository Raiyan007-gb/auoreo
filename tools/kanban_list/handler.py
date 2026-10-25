from auoreo.teams.tooling import attempt, teams_for
from auoreo.tools.context import ToolContext, ToolResult


def _fmt(row) -> str:
    worker = f" worker={row.worker}" if row.worker else ""
    return f"#{row.id} [{row.status}] {row.title} (board={row.board}{worker} failures={row.failures})"


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    kb = teams_for(ctx).kanban
    rows = await attempt(kb.list_tasks(args.get("board", "default"), args.get("status")))
    text = "\n".join(_fmt(r) for r in rows) or "no tasks"
    return ToolResult(text, meta={"tasks": [r.id for r in rows]})
