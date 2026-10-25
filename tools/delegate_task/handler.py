import uuid

from auoreo.teams.tooling import attempt, teams_for
from auoreo.tools.context import ToolContext, ToolError, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    teams = teams_for(ctx)
    depth = await teams.depth(ctx.session_id)
    if depth >= teams.max_depth:
        raise ToolError(
            f"delegate_task is not available to leaf agents (depth {depth} >= "
            f"teams_max_depth={teams.max_depth}); do the task yourself"
        )
    name = args.get("name") or f"task_{uuid.uuid4().hex[:6]}"
    prompt = args["goal"]
    if args.get("context"):
        prompt += f"\n\nContext:\n{args['context']}"
    background = bool(args.get("background", False))
    member = await attempt(
        teams.spawn(
            ctx.session_id,
            agent=args.get("agent") or "default",
            prompt=prompt,
            name=name,
            background=background,
            model=args.get("model"),
            output_schema=args.get("output_schema"),
        )
    )
    meta = {"task_id": member.task_id, "name": name, "agent": member.agent, "team": member.team_id}
    if background:
        return ToolResult(
            f"Started background task '{name}' (task-id {member.task_id}, agent {member.agent}). "
            "Its result will arrive later as a task-notification message; "
            f"call wait_agent(name='{name}') to block on it.",
            meta={**meta, "status": member.status},
        )
    return ToolResult(
        member.notification(),
        is_error=member.status != "completed",
        meta={**meta, "status": member.status, "usage": member.usage},
    )
