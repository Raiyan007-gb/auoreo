"""Update a goal; the calling session is appended to its evidence automatically."""

from auoreo.goals.tooling import attempt, fmt, goals_for
from auoreo.tools.context import ToolContext, ToolError, ToolResult

FIELDS = ("status", "progress", "title", "description")


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    goals = goals_for(ctx)
    gid = int(args["id"])
    changes = {k: args[k] for k in FIELDS if k in args}
    into = args.get("absorb_into")
    if into is None and not changes:
        raise ToolError("nothing to update: pass status, progress, title, description or absorb_into")
    row = await attempt(goals.update(gid, evidence_session=ctx.session_id, **changes))
    if into is not None:
        target = await attempt(goals.absorb(gid, int(into)))
        return ToolResult(
            f"goal #{gid} absorbed into " + fmt(target), meta={"id": gid, "absorbed_into": target.id}
        )
    return ToolResult("updated goal " + fmt(row), meta={"id": row.id})
