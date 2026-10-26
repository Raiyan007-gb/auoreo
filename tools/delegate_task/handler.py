"""delegate_task: one subagent (`goal`) or a batch (`tasks[]`, spec §16.1) run with at most
`settings.tools.delegate_batch_concurrency` (3) children at a time; a foreground batch joins
every child's <task-notification> into one result, in task order."""

import asyncio
import uuid
from typing import Any

from auoreo.teams.tooling import attempt, teams_for
from auoreo.tools.context import ToolContext, ToolError, ToolResult

MAX_BATCH = 10


def _prompt(task: dict[str, Any]) -> str:
    prompt = str(task["goal"])
    if task.get("context"):
        prompt += f"\n\nContext:\n{task['context']}"
    return prompt


async def _spawn(teams: Any, ctx: ToolContext, task: dict[str, Any], name: str, background: bool) -> Any:
    return await attempt(
        teams.spawn(
            ctx.session_id,
            agent=task.get("agent") or "default",
            prompt=_prompt(task),
            name=name,
            background=background,
            model=task.get("model"),
            output_schema=task.get("output_schema"),
            role=task.get("role"),
        )
    )


async def _single(teams: Any, ctx: ToolContext, args: dict) -> ToolResult:
    name = args.get("name") or f"task_{uuid.uuid4().hex[:6]}"
    background = bool(args.get("background", False))
    member = await _spawn(teams, ctx, args, name, background)
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


def _names(tasks: list[dict[str, Any]]) -> list[str]:
    batch = uuid.uuid4().hex[:6]
    names = [t.get("name") or f"task_{batch}_{i}" for i, t in enumerate(tasks, 1)]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ToolError(f"duplicate task names in tasks[]: {', '.join(dupes)}")
    return names


async def _batch(teams: Any, ctx: ToolContext, args: dict) -> ToolResult:
    tasks: list[dict[str, Any]] = list(args["tasks"])
    if len(tasks) > MAX_BATCH:
        raise ToolError(f"at most {MAX_BATCH} tasks per batch (got {len(tasks)})")
    names = _names(tasks)
    background = bool(args.get("background", False))
    svc = ctx.services.get("services")
    limit = int(
        getattr(getattr(getattr(svc, "settings", None), "tools", None), "delegate_batch_concurrency", 3)
    )
    gate = asyncio.Semaphore(max(1, limit))
    await attempt(teams.ensure_team(ctx.session_id))  # one team before the concurrent spawns

    async def one(task: dict[str, Any], name: str) -> tuple[str, Any]:
        async with gate:
            try:
                return name, await _spawn(teams, ctx, task, name, background)
            except ToolError as exc:
                return name, exc

    results = await asyncio.gather(*(one(t, n) for t, n in zip(tasks, names, strict=True)))
    parts: list[str] = []
    rows: list[dict[str, Any]] = []
    for name, res in results:
        if isinstance(res, ToolError):
            parts.append(f'<task-error name="{name}">{res}</task-error>')
            rows.append({"name": name, "status": "failed", "error": str(res)})
            continue
        rows.append({"name": name, "task_id": res.task_id, "agent": res.agent, "status": res.status})
        parts.append(f"- '{name}' task-id {res.task_id} ({res.agent})" if background else res.notification())
    done = sum(r["status"] == "completed" for r in rows)
    if background:
        started = sum("task_id" in r for r in rows)
        head = (
            f"Started {started}/{len(rows)} background tasks (at most {limit} run at once); each result "
            "arrives later as a task-notification message:"
        )
        return ToolResult("\n".join([head, *parts]), is_error=started == 0, meta={"tasks": rows})
    head = f"batch of {len(rows)} tasks: {done} completed (concurrency {limit})"
    return ToolResult("\n\n".join([head, *parts]), is_error=done == 0, meta={"tasks": rows})


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    teams = teams_for(ctx)
    depth = await teams.depth(ctx.session_id)
    if depth >= teams.max_depth:
        raise ToolError(
            f"delegate_task is not available to leaf agents (depth {depth} >= "
            f"teams_max_depth={teams.max_depth}); do the task yourself"
        )
    if bool(args.get("goal")) == bool(args.get("tasks")):
        raise ToolError("give either goal (one task) or tasks[] (a batch), not both")
    return await (_batch(teams, ctx, args) if args.get("tasks") else _single(teams, ctx, args))
