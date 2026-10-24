import asyncio
import contextlib
import os
import signal

from auoreo.tools.context import ToolContext, ToolResult

CAP = 30_000


def _clip(text: str) -> str:
    if len(text) <= CAP:
        return text
    head, tail = text[: CAP // 2], text[-CAP // 4 :]
    return f"{head}\n… [{len(text) - len(head) - len(tail)} chars omitted] …\n{tail}"


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    timeout = int(args.get("timeout_s", 120))
    proc = await asyncio.create_subprocess_exec(
        "bash",
        "-c",
        args["command"],
        cwd=str(ctx.cwd),
        env=ctx.env or None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (TimeoutError, asyncio.CancelledError):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()
        if asyncio.current_task() and asyncio.current_task().cancelling():
            raise
        return ToolResult(f"command timed out after {timeout}s", is_error=True)
    text = _clip(out.decode("utf-8", errors="replace"))
    code = proc.returncode
    return ToolResult(f"{text}\n[exit code {code}]".lstrip(), is_error=code != 0, meta={"exit_code": code})
