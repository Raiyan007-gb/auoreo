from auoreo.teams.tooling import attempt, my_team
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    teams, team, me = await my_team(ctx)
    row = await attempt(teams.board.put(team.id, args["key"], args["value"], me))
    return ToolResult(
        f"board:{row.key} v{row.version} ({len(row.value.encode('utf-8'))} bytes) by {me}; "
        f"mail teammates the pointer 'board:{row.key}', not the content",
        meta={"team": team.id, "key": row.key, "version": row.version},
    )
