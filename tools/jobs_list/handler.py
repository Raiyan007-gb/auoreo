"""This session's background jobs (exec_command processes + terminal_pty sessions)."""

from auoreo.tools.context import ToolContext, ToolResult
from auoreo.tools.pty_jobs import jobs_of, jobs_table


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    jobs = jobs_of(ctx)
    if not jobs:
        return ToolResult("no background jobs", meta={"jobs": []})
    return ToolResult(jobs_table(jobs), meta={"jobs": [j.id for j in jobs]})
