"""Open a persistent local PTY (spec §9.3 `terminal_pty`). Permission rules and floors ran
in the executor on `cmd`; like `exec_command` it refuses under an enforcing sandbox."""

import os
import time

from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.pty_jobs import guarded, pty_registry, render_pty, services_of, set_winsize
from auoreo.tools.shell_sessions import refuse_if_sandboxed


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    refuse_if_sandboxed(ctx, "terminal_pty")
    if os.name != "posix":
        raise ToolError("terminal_pty needs a POSIX host (pty); use exec_command instead")
    workdir = ctx.resolve_path(args.get("workdir") or ".")
    root = ctx.cwd.resolve()
    if workdir != root and root not in workdir.parents:
        await ctx.authorize("terminal_pty", "edit", str(workdir), default="ask")
    if not workdir.is_dir():
        raise ToolError(f"workdir is not a directory: {workdir}")
    reg = pty_registry(services_of(ctx))
    cmd = str(args["cmd"])
    env = {**ctx.env, "TERM": str(args.get("term") or "dumb")}
    p = await guarded(
        reg.start(session_id=ctx.session_id, cmd=cmd, cwd=workdir, env=env, tty=True, granted=cmd)
    )
    reg.started[p.id] = time.monotonic()
    set_winsize(p, int(args.get("rows", 40)), int(args.get("cols", 120)))
    async with reg.lock(p.id):
        text, dropped = await reg.collect(p, int(args.get("yield_time_ms", 1000)) / 1000)
    return render_pty(p, text, dropped, int(args.get("max_output_tokens", 4000)))
