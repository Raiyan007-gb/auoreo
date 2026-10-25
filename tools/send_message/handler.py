from auoreo.teams.mailbox import BROADCAST, BROADCAST_SUMMARY_WORDS
from auoreo.teams.tooling import attempt, my_team
from auoreo.tools.context import ToolContext, ToolError


async def run(args: dict, ctx: ToolContext) -> str:
    teams, team, me = await my_team(ctx)
    summary = args.get("summary", "")
    words = len(summary.split())
    if args["to"] == BROADCAST and not 1 <= words <= BROADCAST_SUMMARY_WORDS:
        raise ToolError(
            f'broadcast to "*" needs a summary of 1–{BROADCAST_SUMMARY_WORDS} words (got {words})'
        )
    if len(args["message"]) > 500 and not summary.strip():
        raise ToolError(
            "messages over 500 characters need a summary (put big results on the board: board_put)"
        )
    rows = await attempt(teams.mail.send(team.id, me, args["to"], args["message"], summary))
    if args["to"] != BROADCAST:
        await teams.supervisor.on_mail(team.id, ctx.session_id, args["to"])
    return f"delivered to {', '.join(r.recipient for r in rows) or 'nobody'}"
