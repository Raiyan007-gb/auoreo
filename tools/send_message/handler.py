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
    if args["to"].startswith(("bridge:", "uds:")):  # --- W2-B: another instance (remote/bridge.py)
        return await _remote(args, ctx, teams, me, summary)
    rows = await attempt(teams.mail.send(team.id, me, args["to"], args["message"], summary))
    if args["to"] != BROADCAST:
        await teams.supervisor.on_mail(team.id, ctx.session_id, args["to"])
    return f"delivered to {', '.join(r.recipient for r in rows) or 'nobody'}"


async def _remote(args: dict, ctx: ToolContext, teams, me: str, summary: str) -> str:
    from auoreo.remote import bridge

    try:
        out = await bridge.send(
            teams.s,
            sender_member=me,
            to=args["to"],
            message=args["message"],
            summary=summary,
            session_id=ctx.session_id,
        )
    except bridge.BridgeError as exc:
        raise ToolError(str(exc)) from exc
    return f"delivered to {args['to']} (their team {out.team}, mail #{out.mail_id})"
