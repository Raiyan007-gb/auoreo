from auoreo.goals.tooling import attempt, fmt, goals_for
from auoreo.tools.context import ToolContext, ToolError, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    goals = goals_for(ctx)
    if "id" in args:
        row = await goals.get(int(args["id"]))
        if row is None:
            raise ToolError(f"goal {args['id']} not found")
        return ToolResult(fmt(row, long=True), meta={"id": row.id})
    status = args.get("status", "active")
    rows = await attempt(goals.list(status))
    if not rows:
        return ToolResult("no goals" if status == "all" else f"no {status} goals", meta={"ids": []})
    return ToolResult("\n".join(fmt(r) for r in rows), meta={"ids": [r.id for r in rows]})
