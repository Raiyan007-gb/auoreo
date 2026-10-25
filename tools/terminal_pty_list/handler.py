"""This session's terminal_pty sessions as a markdown table."""

from auoreo.tools.context import ToolContext, ToolResult
from auoreo.tools.pty_jobs import jobs_of, jobs_table


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    rows = [j for j in jobs_of(ctx) if j.kind == "pty"]
    if not rows:
        return ToolResult("no open terminal_pty sessions", meta={"sessions": []})
    return ToolResult(jobs_table(rows), meta={"sessions": [j.id for j in rows]})
