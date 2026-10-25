"""Kill a terminal_pty session's process group and drop it from the registry."""

from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.pty_jobs import pty_registry, services_of, strip_ansi
from auoreo.tools.shell_sessions import ShellError, clip_tokens


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    reg = pty_registry(services_of(ctx))
    sid = str(args["session_id"])
    try:
        p = reg.get(ctx.session_id, sid)
    except ShellError as exc:
        raise ToolError(str(exc)) from exc
    async with reg.lock(sid):
        text, _ = p.take()
        await reg.kill(p)
    max_tokens = int(args.get("max_output_tokens", 2000))
    lines = [clip_tokens(strip_ansi(text), max_tokens).rstrip("\n")] if text.strip() else []
    lines.append(f"[closed] {sid}")
    return ToolResult("\n".join(lines), meta={"session_id": sid, "closed": True})
