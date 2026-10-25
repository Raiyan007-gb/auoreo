"""Poll a terminal_pty session's output (new since the last read, or a non-consuming tail)."""

from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.pty_jobs import peek, pty_registry, render_pty, services_of
from auoreo.tools.shell_sessions import ShellError


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    reg = pty_registry(services_of(ctx))
    sid = str(args["session_id"])
    try:
        p = reg.get(ctx.session_id, sid)
    except ShellError as exc:
        raise ToolError(str(exc)) from exc
    max_tokens = int(args.get("max_output_tokens", 4000))
    raw = bool(args.get("raw"))
    async with reg.lock(sid):
        if args.get("tail_chars"):
            text = peek(p, int(args["tail_chars"]))
            p.consumed = p.base + len(p.buf)
            return render_pty(p, text, 0, max_tokens, raw=raw)
        text, dropped = await reg.collect(p, int(args.get("yield_time_ms", 500)) / 1000)
    return render_pty(p, text, dropped, max_tokens, raw=raw)
