"""Poll a background job: an exec_command process through its environment backend, or a
terminal_pty session through the PTY registry. Polling writes nothing."""

from auoreo.tools import environments
from auoreo.tools.context import ToolContext, ToolResult
from auoreo.tools.pty_jobs import find_job, pty_registry, render_pty, services_of


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    job = find_job(ctx, str(args["job_id"]))
    yield_ms = int(args.get("yield_time_ms", 0))
    max_tokens = int(args.get("max_output_tokens", 4000))
    if job.kind == "pty":
        reg = pty_registry(services_of(ctx))
        p = reg.get(ctx.session_id, job.id)
        async with reg.lock(job.id):
            text, dropped = await reg.collect(p, yield_ms / 1000)
        return render_pty(p, text, dropped, max_tokens)
    rec = environments.lookup(ctx, job.id)
    poll = {"chars": "", "yield_time_ms": yield_ms, "max_output_tokens": max_tokens}
    return await environments.write_stdin(ctx, rec, job.id, poll)
