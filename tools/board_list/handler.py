from auoreo.teams.tooling import attempt, my_team
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    teams, team, _ = await my_team(ctx)
    prefix = args.get("prefix", "")
    rows = await attempt(teams.board.list(team.id, prefix))
    if not rows:
        return ToolResult(
            f"the board of team {team.name} has no keys" + (f" under '{prefix}'" if prefix else "")
        )
    lines = [f"- {r.key} v{r.version} by {r.producer} ({len(r.value.encode('utf-8'))} bytes)" for r in rows]
    return ToolResult(f"{len(rows)} key(s) on team {team.name}'s board:\n" + "\n".join(lines))
