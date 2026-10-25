"""Write to (or poll) a persistent exec_command process.

Authorisation: the process must belong to this AUOREO session and carry the `bash` grant
its exec_command was allowed under. The `bash` permission is then evaluated with the
pattern `stdin:<session_id>`: a deny (rule or floor) blocks, while `ask` is covered by the
stored grant. The bash hardline floors are also applied to every line written.
"""

from auoreo.core.permissions import PermissionEngine
from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.shell_sessions import ShellError, refuse_if_sandboxed, registry_for, render


def _authorize(ctx: ToolContext, sid: str, chars: str) -> None:
    engine = ctx.services.get("engine")
    if engine is not None:
        decision = engine.evaluate("bash", f"stdin:{sid}", "allow", interactive=ctx.interactive)
        if decision.action == "deny":
            raise ToolError(f"permission denied for bash `stdin:{sid}` ({decision.detail})")
    floors = engine or PermissionEngine([])
    for line in chars.replace("\r", "\n").splitlines():
        if line.strip() and (why := floors.floor("bash", line.strip(), interactive=ctx.interactive)):
            raise ToolError(f"permission denied (floor: {why}). Do not retry unchanged.")


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    refuse_if_sandboxed(ctx, "write_stdin")
    reg = registry_for(ctx.services.get("services"))
    sid = str(args["session_id"])
    try:
        p = reg.get(ctx.session_id, sid)
    except ShellError as exc:
        raise ToolError(str(exc)) from exc
    if not p.granted:
        raise ToolError(f"process {sid} has no bash grant; start it again with exec_command")
    chars = str(args.get("chars", ""))
    _authorize(ctx, sid, chars)
    try:
        await reg.write(p, chars)
    except ShellError as exc:
        raise ToolError(str(exc)) from exc
    text, dropped = await reg.collect(p, int(args.get("yield_time_ms", 2000)) / 1000)
    return render(p, text, dropped, int(args.get("max_output_tokens", 4000)))
