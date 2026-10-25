from auoreo.teams.tooling import attempt, my_team
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    teams, team, _ = await my_team(ctx)
    out = await attempt(teams.wait(team.id, args["name"], args.get("timeout_ms")))
    head = f"{out['name']}: status={out['status']} running={out['running']} timed_out={out['timed_out']}"
    body = f"\n{out['result']}" if out["result"] else ""
    return ToolResult(head + body, meta=out)
