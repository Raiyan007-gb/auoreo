"""Persistent shell: start `bash -lc <cmd>` in the session's execution environment (the
local machine by default; see `use_environment`) and return output until exit or
`yield_time_ms`. Permission rules and floors ran in the executor before this handler."""

from auoreo.tools import environments
from auoreo.tools.context import ToolContext, ToolResult


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    return await environments.exec_command(ctx, args)
