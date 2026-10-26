"""cronjob (spec §9, §16.7): create / list / remove reminders and cron jobs. Jobs are written to
`<home>/jobs/<name>.yaml` (see `auoreo.automation.cron_store`); create asks for approval."""

from typing import Any

from auoreo.automation.cron_store import CronStoreError, create, remove
from auoreo.automation.scheduler import scheduler_of
from auoreo.automation.views import job_infos
from auoreo.tools.context import ToolContext, ToolError, ToolResult

FIELDS = (
    "name",
    "schedule",
    "prompt",
    "description",
    "tz",
    "agent",
    "deliver",
    "timeout_s",
    "model",
    "provider",
    "script",
    "context_from",
    "workdir",
    "notify_on_complete",
)


def _services(ctx: ToolContext) -> Any:
    svc = ctx.services.get("services")
    if svc is None:
        raise ToolError("cronjob needs the server services")
    return svc


async def _list(svc: Any, args: dict, ctx_session: str = "") -> ToolResult:
    infos = await job_infos(scheduler_of(svc))
    lines = [
        f"- {j.name}: {j.schedule}{' (' + j.tz + ')' if j.tz else ''} agent={j.agent} "
        f"{'paused' if j.paused else ('enabled' if j.enabled else 'disabled')} next={j.next_run_at}"
        for j in infos
    ]
    return ToolResult("\n".join([f"{len(infos)} job(s)", *lines]), meta={"jobs": [j.name for j in infos]})


async def _create(svc: Any, args: dict, ctx_session: str) -> ToolResult:
    missing = [k for k in ("name", "schedule", "prompt") if not args.get(k)]
    if missing:
        raise ToolError(f"action=create needs {', '.join(missing)}")
    data = {k: args[k] for k in FIELDS if args.get(k) is not None}
    data.setdefault("description", str(args["prompt"]).strip().splitlines()[0][:120] or args["name"])
    data["origin_session"] = ctx_session  # --- I3: notify_on_complete / deliver: [origin]
    dry = bool(args.get("dry_run"))
    try:
        p = await create(svc, data, dry_run=dry)
    except CronStoreError as exc:
        raise ToolError(str(exc)) from exc
    info = p.as_dict()
    fires = "\n".join(f"  {f}" for f in info["next_fires"]) or "  (none)"
    head = "dry run (nothing saved)" if dry else f"created job '{info['name']}'"
    shape = f"[{info['kind']}, tz {info['tz']}, {info['catch_up']}]"
    text = f"{head}: {info['schedule']} {shape}\nnext fires:\n{fires}"
    return ToolResult(text, meta={**info, "dry_run": dry})


async def _remove(svc: Any, args: dict, ctx_session: str = "") -> ToolResult:
    if not args.get("name"):
        raise ToolError("action=remove needs name")
    try:
        await remove(svc, args["name"])
    except CronStoreError as exc:
        raise ToolError(str(exc)) from exc
    return ToolResult(f"removed job '{args['name']}'", meta={"name": args["name"]})


ACTIONS = {"list": _list, "create": _create, "remove": _remove}


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    action = str(args.get("action") or ("create" if args.get("dry_run") else "list"))
    if args.get("dry_run") and action != "create":  # dry_run skips approval: never let it remove
        raise ToolError("dry_run applies to action=create only")
    return await ACTIONS[action](_services(ctx), args, ctx.session_id)
