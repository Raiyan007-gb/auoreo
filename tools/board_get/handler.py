from auoreo.teams.tooling import attempt, my_team
from auoreo.tools.context import ToolContext, ToolError, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    teams, team, _ = await my_team(ctx)
    row = await attempt(teams.board.get(team.id, args["key"]))
    if row is None:
        raise ToolError(f"no board key '{args['key']}' in team {team.name} (board_list shows the keys)")
    head = f"board:{row.key} v{row.version} by {row.producer} (updated {row.updated_at:%Y-%m-%d %H:%M:%S}Z)"
    return ToolResult(
        f"{head}\n\n{row.value}", meta={"team": team.id, "key": row.key, "version": row.version}
    )
