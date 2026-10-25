"""Type into a terminal_pty session. Rides on the `bash` grant the PTY was opened under;
`bash stdin:<id>` denies and the bash floors still apply to every typed line."""

from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.pty_jobs import (
    authorize_stdin,
    guarded,
    keys_to_chars,
    pty_registry,
    render_pty,
    services_of,
)
from auoreo.tools.shell_sessions import ShellError


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    reg = pty_registry(services_of(ctx))
    sid = str(args["session_id"])
    try:
        p = reg.get(ctx.session_id, sid)
    except ShellError as exc:
        raise ToolError(str(exc)) from exc
    chars = str(args.get("chars", "")) + keys_to_chars(list(args.get("keys") or []))
    authorize_stdin(ctx, sid, chars)
    async with reg.lock(sid):
        await guarded(reg.write(p, chars))
        text, dropped = await reg.collect(p, int(args.get("yield_time_ms", 1000)) / 1000)
    return render_pty(p, text, dropped, int(args.get("max_output_tokens", 4000)), raw=bool(args.get("raw")))
