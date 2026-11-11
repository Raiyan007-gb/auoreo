"""Copy one file to or from the session's SSH environment (M7 `m7.ssh.transfer`).

All checks (flag, ssh grant, one-shot `sftp` permission, local file rules, size cap) live in
the engine module `auoreo.tools.transfers`."""

from auoreo.tools import transfers
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    return await transfers.transfer(ctx, args)
