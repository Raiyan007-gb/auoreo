"""Persistent shell: start `bash -lc <cmd>` and return output until exit or `yield_time_ms`."""

import os

from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.shell_sessions import ShellError, refuse_if_sandboxed, registry_for, render


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    tty = bool(args.get("tty", False))
    if tty and os.name != "posix":
        raise ToolError("tty=true needs a POSIX host (pty); run without tty")
    refuse_if_sandboxed(ctx, "exec_command")
    workdir = ctx.resolve_path(args.get("workdir") or ".")
    root = ctx.cwd.resolve()
    if workdir != root and root not in workdir.parents:  # outside cwd: the edit rules decide
        await ctx.authorize("exec_command", "edit", str(workdir), default="ask")
    if not workdir.is_dir():
        raise ToolError(f"workdir is not a directory: {workdir}")
    reg = registry_for(ctx.services.get("services"))
    try:
        p = await reg.start(
            session_id=ctx.session_id,
            cmd=args["cmd"],
            cwd=workdir,
            env=dict(ctx.env),  # the executor's scrubbed child_env
            tty=tty,
            granted=args["cmd"],  # the executor allowed `bash` for this exact pattern
        )
    except ShellError as exc:
        raise ToolError(str(exc)) from exc
    text, dropped = await reg.collect(p, int(args.get("yield_time_ms", 10000)) / 1000)
    return render(p, text, dropped, int(args.get("max_output_tokens", 4000)))
