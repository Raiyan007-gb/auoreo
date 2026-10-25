from auoreo.teams.tooling import attempt, teams_for
from auoreo.tools.context import ToolContext


async def run(args: dict, ctx: ToolContext) -> str:
    teams = teams_for(ctx)
    team = await attempt(teams.create_team(ctx.session_id, args["name"], args.get("description", "")))
    return f"created team {team.name} ({team.id}); you are its lead"
