"""Write to (or poll) a persistent exec_command process.

Authorisation: the process must belong to this AUOREO session and carry the `bash` grant
its exec_command was allowed under. The `bash` permission is then evaluated with the
pattern `stdin:<session_id>`: a deny (rule or floor) blocks, while `ask` is covered by the
stored grant. The bash hardline floors are also applied to every line written. The input
goes to whichever environment backend started the process.
"""

from auoreo.core.permissions import PermissionEngine
from auoreo.tools import environments
from auoreo.tools.context import ToolContext, ToolError, ToolResult


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
    sid = str(args["session_id"])
    rec = environments.lookup(ctx, sid)
    if not rec.granted:
        raise ToolError(f"process {sid} has no bash grant; start it again with exec_command")
    _authorize(ctx, sid, str(args.get("chars", "")))
    return await environments.write_stdin(ctx, rec, sid, args)
