from auoreo.teams.tooling import attempt, my_team
from auoreo.tools.context import ToolContext


async def run(args: dict, ctx: ToolContext) -> str:
    teams, team, me = await my_team(ctx)
    lines = [f"team {team.name} ({team.id}); you are '{me}'"]
    for p in await attempt(teams.peers(team.id)):
        live = "running" if p["running"] else "idle"
        lines.append(f"- {p['name']}: agent={p['agent']} status={p['status']} {live} depth={p['depth']}")
    return "\n".join(lines)
