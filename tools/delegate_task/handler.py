"""delegate_task (spec §16.1/§16.2/§25.11): `action=spawn` (default) runs one subagent (`goal`)
or a batch (`tasks[]`, at most `settings.tools.delegate_batch_concurrency` (3) at a time; a
foreground batch joins every child's <task-notification> into one result, in task order).
`mode=fork` seeds the child with a summary of this conversation, `mode=exact` with the whole
conversation + this agent's exact system prompt and tools (provider cache hits);
`allowedTools`, `inherit_mcp_toolsets` and `handoff` shape the child per spawn; `budget{max_tokens,max_usd,
timeout_ms}` bounds it. `action=list|steer|stop` manage this session's children; they are
synchronous and bypass the pause switch and the depth limit. `worktree=true` (M7,
`m7.orch.worktrees`) runs the child in its own git worktree on branch `auoreo/<name>-<hex>`
(`teams.worktree_spawn`); merging the branch back is not this tool's job."""

import asyncio
import json
import uuid
from typing import Any

from auoreo.runtime.subtask import WorktreeError
from auoreo.teams import worktree_spawn
from auoreo.teams.fork import FrozenTurn
from auoreo.teams.tooling import attempt, teams_for
from auoreo.tools.context import ToolContext, ToolError, ToolResult

MAX_BATCH = 10


def _prompt(task: dict[str, Any]) -> str:
    prompt = str(task["goal"])
    if task.get("context"):
        prompt += f"\n\nContext:\n{task['context']}"
    return prompt


async def _tree(ctx: ToolContext, task: dict[str, Any], args: dict, name: str) -> Any:
    """The task's own git worktree when `worktree` is set (M7 W4); errors start no child."""
    if not _pick(task, args, "worktree"):
        return None
    try:
        return await worktree_spawn.prepare(ctx.services["services"], f"{name}-{uuid.uuid4().hex[:4]}")
    except (WorktreeError, OSError) as exc:
        raise ToolError(str(exc)) from exc


async def _spawn(
    teams: Any,
    ctx: ToolContext,
    task: dict[str, Any],
    name: str,
    background: bool,
    args: dict,
    tree: Any = None,
) -> Any:
    try:
        return await _start(teams, ctx, task, name, background, args, tree)
    except BaseException:  # a failed or cancelled spawn: the worktree made for it goes too
        if tree is not None:  # (a dirty or committed one is kept by `discard_worktree`)
            await asyncio.shield(worktree_spawn.rollback(ctx.services["services"], [tree]))
        raise


async def _start(
    teams: Any, ctx: ToolContext, task: dict[str, Any], name: str, background: bool, args: dict, tree: Any
) -> Any:
    return await attempt(
        teams.spawn(
            ctx.session_id,
            agent=task.get("agent") or "default",
            prompt=_prompt(task) + (worktree_spawn.child_note(tree) if tree else ""),
            name=name,
            background=background,
            model=task.get("model"),
            output_schema=task.get("output_schema"),
            role=task.get("role"),
            mode=task.get("mode") or args.get("mode") or "spawn",
            budget=task.get("budget") or args.get("budget"),
            allowed_tools=_pick(task, args, "allowedTools"),
            inherit_mcp_toolsets=_pick(task, args, "inherit_mcp_toolsets") or False,
            handoff=bool(_pick(task, args, "handoff")),
            frozen=_frozen(ctx),
            cwd=tree.path if tree else None,
            worktree=_created(tree, ctx, name) if tree else None,
        )
    )


def _created(tree: Any, ctx: ToolContext, name: str) -> dict[str, Any]:
    """`worktree.created` data with the child's `task_ref` (`delegate:<parent>/<name>`)."""
    return worktree_spawn.payload(tree, task_ref=worktree_spawn.delegate_ref(ctx.session_id, name))


def _pick(task: dict[str, Any], args: dict, key: str) -> Any:
    return task[key] if key in task else args.get(key)


def _frozen(ctx: ToolContext) -> FrozenTurn | None:
    """The parent's running turn (spec, options, context) for `mode: exact` forks."""
    spec = ctx.services.get("spec")
    if spec is None or "turn_msg" not in ctx.services:
        return None
    return FrozenTurn(
        spec, ctx.services.get("options"), ctx.services.get("context") or "", ctx.services["turn_msg"]
    )


async def _single(teams: Any, ctx: ToolContext, args: dict) -> ToolResult:
    name = args.get("name") or f"task_{uuid.uuid4().hex[:6]}"
    background = bool(args.get("background", False))
    tree = await _tree(ctx, args, args, name)
    member = await _spawn(teams, ctx, args, name, background, args, tree)
    note = f"\n{worktree_spawn.summary(tree)}" if tree else ""
    meta = {
        "task_id": member.task_id,
        "subagent_id": member.subagent_id,
        "name": name,
        "agent": member.agent,
        "team": member.team_id,
        "role": member.role,
        **({"worktree": {"path": str(tree.path), "branch": tree.branch}} if tree else {}),
    }
    if background:
        return ToolResult(
            f"Started background task '{name}' (task-id {member.task_id}, agent {member.agent}). "
            "Its result will arrive later as a task-notification message; "
            f"call wait_agent(name='{name}') to block on it.{note}",
            meta={**meta, "status": member.status},
        )
    return ToolResult(
        member.notification() + note,
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


async def _batch_trees(
    ctx: ToolContext, tasks: list[dict[str, Any]], names: list[str], args: dict
) -> dict[str, Any]:
    """One worktree per task that asks for one, created serially and all-or-nothing: when
    one fails, those made in this call are removed and no child starts."""
    trees: dict[str, Any] = {}
    for task, name in zip(tasks, names, strict=True):
        try:
            trees[name] = await _tree(ctx, task, args, name)
        except BaseException:  # failed or cancelled: none of this call's worktrees stays
            made = [t for t in trees.values() if t]
            await asyncio.shield(worktree_spawn.rollback(ctx.services["services"], made))
            raise
    return trees


async def _batch(teams: Any, ctx: ToolContext, args: dict) -> ToolResult:
    tasks: list[dict[str, Any]] = list(args["tasks"])
    schemas = list(args.get("task_schemas") or [])  # spec §24.10: coerced per task (index-aligned)
    if len(schemas) > len(tasks):
        raise ToolError(f"task_schemas has {len(schemas)} entries for {len(tasks)} tasks")
    tasks = [
        {**t, "output_schema": schemas[i]} if i < len(schemas) and not t.get("output_schema") else t
        for i, t in enumerate(tasks)
    ]
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
    trees = await _batch_trees(ctx, tasks, names, args)

    started: set[str] = set()

    async def one(task: dict[str, Any], name: str) -> tuple[str, Any]:
        try:
            async with gate:
                started.add(name)  # from here `_spawn` owns the rollback of its worktree
                return name, await _spawn(teams, ctx, task, name, background, args, trees[name])
        except ToolError as exc:
            return name, exc
        except BaseException:  # cancelled while waiting for the gate: `_spawn` never ran
            if name not in started and trees[name] is not None:
                await asyncio.shield(worktree_spawn.rollback(ctx.services["services"], [trees[name]]))
            raise

    results = await asyncio.gather(*(one(t, n) for t, n in zip(tasks, names, strict=True)))
    parts: list[str] = []
    rows: list[dict[str, Any]] = []
    for name, res in results:
        if isinstance(res, ToolError):
            parts.append(f'<task-error name="{name}">{res}</task-error>')
            rows.append({"name": name, "status": "failed", "error": str(res)})
            continue
        rows.append({"name": name, "task_id": res.task_id, "agent": res.agent, "status": res.status})
        if trees[name] is not None:
            rows[-1]["worktree"] = {"path": str(trees[name].path), "branch": trees[name].branch}
            parts.append(worktree_spawn.summary(trees[name]))
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


def _ref(args: dict) -> str:
    ref = args.get("subagent_id") or args.get("name")
    if not ref:
        raise ToolError(f"action={args.get('action')} needs subagent_id (task id or name)")
    return str(ref)


async def _list(teams: Any, ctx: ToolContext, args: dict) -> ToolResult:
    rows = await attempt(teams.control.children(ctx.session_id))
    lines = [
        f"- {r['name']} ({r['agent']}, {r['role'] or '?'}) id={r['subagent_id']} task={r['task_id']} "
        f"status={r['status']} running={r['running']}"
        for r in rows
    ]
    paused = " (delegation is paused)" if teams.control.paused else ""
    head = f"{len(rows)} subagent(s){paused}"
    return ToolResult(
        "\n".join([head, *lines]), meta={"subagents": json.loads(json.dumps(rows, default=str))}
    )


async def _steer(teams: Any, ctx: ToolContext, args: dict) -> ToolResult:
    if not args.get("message"):
        raise ToolError("action=steer needs message")
    out = await attempt(teams.control.steer(ctx.session_id, _ref(args), str(args["message"])))
    return ToolResult(f"steered {out['name']}: the message arrives at its next step", meta=out)


async def _stop(teams: Any, ctx: ToolContext, args: dict) -> ToolResult:
    out = await attempt(teams.control.stop(ctx.session_id, _ref(args)))
    text = f"stopped {out['name']}" if out["stopped"] else f"{out['name']} was not running"
    return ToolResult(text, meta=out)


async def _spawn_action(teams: Any, ctx: ToolContext, args: dict) -> ToolResult:
    depth = await teams.depth(ctx.session_id)
    if depth >= teams.max_depth:
        raise ToolError(
            f"delegate_task is not available to leaf agents (depth {depth} >= "
            f"teams_max_depth={teams.max_depth}); do the task yourself"
        )
    if teams.roles.get(ctx.session_id) == "leaf":
        raise ToolError("delegate_task is not available to leaf subagents; do the task yourself")
    if bool(args.get("goal")) == bool(args.get("tasks")):
        raise ToolError("give either goal (one task) or tasks[] (a batch), not both")
    return await (_batch(teams, ctx, args) if args.get("tasks") else _single(teams, ctx, args))


ACTIONS = {"spawn": _spawn_action, "list": _list, "steer": _steer, "stop": _stop}


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    teams = teams_for(ctx)
    return await ACTIONS[str(args.get("action") or "spawn")](teams, ctx, args)
