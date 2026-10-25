from auoreo.teams.tooling import attempt, my_team
from auoreo.tools.context import ToolContext


async def run(args: dict, ctx: ToolContext) -> str:
    teams, team, _ = await my_team(ctx)
    stopped = await attempt(teams.interrupt(team.id, args["name"]))
    return f"interrupted {args['name']}" if stopped else f"{args['name']} was not running"
