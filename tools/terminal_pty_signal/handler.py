"""Signal a terminal_pty session's process group (only PTYs this session opened)."""

import contextlib
import os

from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.pty_jobs import SIGNALS, pty_registry, render_pty, services_of
from auoreo.tools.shell_sessions import ShellError


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    reg = pty_registry(services_of(ctx))
    sid, name = str(args["session_id"]), str(args.get("signal", "INT"))
    try:
        p = reg.get(ctx.session_id, sid)
    except ShellError as exc:
        raise ToolError(str(exc)) from exc
    if name not in SIGNALS:
        raise ToolError(f"signal {name} is not available on this platform")
    async with reg.lock(sid):
        if p.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(p.pid, SIGNALS[name])
        text, dropped = await reg.collect(p, int(args.get("yield_time_ms", 500)) / 1000)
    res = render_pty(p, text, dropped, 4000)
    res.content = f"sent SIG{name} to {sid}\n{res.content}"
    return res
