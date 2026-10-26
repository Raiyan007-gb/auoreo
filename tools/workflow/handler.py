"""workflow (spec §9.3 P1): run a `workflows/<name>.yaml` chain in a child session from an
agent. Approval-gated by the manifest (`default_action: ask`, pattern = workflow name);
nesting is capped by `h5.workflow.max_depth`."""

from typing import Any

from auoreo.core.settings_h5 import h5_of
from auoreo.tools.context import ToolContext, ToolError, ToolResult

OK = frozenset({"completed", "max_steps"})


async def _depth(svc: Any, session_id: str) -> int:
    depth, cur = 0, await svc.get_session(session_id) if session_id else None
    while cur is not None and cur.parent_id and depth < 50:
        depth += 1
        cur = await svc.get_session(cur.parent_id)
    return depth


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    svc = ctx.services.get("services")
    if svc is None:
        raise ToolError("workflow needs the server services")
    cfg = h5_of(ctx).workflow
    name = args["name"]
    if name not in svc.harness.workflows:
        known = ", ".join(sorted(svc.harness.workflows))
        raise ToolError(f"unknown workflow '{name}' (known: {known})")
    if await _depth(svc, ctx.session_id) >= cfg.max_depth:
        raise ToolError(f"workflow nesting limit reached (h5.workflow.max_depth={cfg.max_depth})")
    child = await svc.create_session(
        title=f"workflow:{name}"[:120], workflow=name, parent_id=ctx.session_id or None
    )
    coord = svc.coordinator
    run_id = await coord.submit(child.id, args["input"], interactive=False)
    meta = {"session_id": child.id, "workflow": name, "run_id": run_id}
    if args.get("background"):
        return ToolResult(
            f"started workflow '{name}' in session {child.id} (background); "
            "read it later with session_search or the sessions API",
            meta=meta | {"status": "running"},
        )
    timeout = float(args.get("timeout_s") or cfg.timeout_s)
    try:
        await coord.wait(child.id, timeout)
    except TimeoutError:
        await coord.cancel(child.id)
        return ToolResult(
            f"workflow '{name}' timed out after {timeout:.0f}s (session {child.id})", True, meta=meta
        )
    res = coord.result(run_id) if run_id else None
    coord.forget(child.id)
    if res is None or res.status not in OK:
        detail = "no result" if res is None else (res.error or {}).get("message") or res.status
        return ToolResult(f"workflow '{name}' failed: {detail} (session {child.id})", True, meta=meta)
    return ToolResult(res.text or "(no output)", meta=meta | {"status": res.status})
