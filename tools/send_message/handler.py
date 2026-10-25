from auoreo.teams.tooling import attempt, my_team
from auoreo.tools.context import ToolContext, ToolError


async def run(args: dict, ctx: ToolContext) -> str:
    teams, team, me = await my_team(ctx)
    summary = args.get("summary", "")
    if len(args["message"]) > 500 and not summary.strip():
        raise ToolError("messages over 500 characters need a summary")
    rows = await attempt(teams.mail.send(team.id, me, args["to"], args["message"], summary))
    return f"delivered to {', '.join(r.recipient for r in rows) or 'nobody'}"
