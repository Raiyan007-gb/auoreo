"""Kill one of this session's background jobs (never another session's)."""

from auoreo.tools.context import ToolContext, ToolResult
from auoreo.tools.pty_jobs import find_job, guarded, kill_job


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    job = find_job(ctx, str(args["job_id"]))
    await guarded(kill_job(ctx, job))
    return ToolResult(f"killed {job.kind} job {job.id} (`{job.cmd[:120]}`)", meta={"job_id": job.id})
