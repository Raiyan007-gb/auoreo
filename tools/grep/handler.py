import asyncio
import shutil

from auoreo.tools.context import ToolContext, ToolError

MODES = {"content": ["-n", "--no-heading"], "files": ["-l"], "count": ["-c"]}


async def run(args: dict, ctx: ToolContext) -> str:
    rg = shutil.which("rg")
    if rg is None:
        raise ToolError("ripgrep (rg) is not installed")
    target = ctx.resolve_path(args.get("path") or ".")
    cmd = [rg, "--color=never", "--hidden", "--glob", "!.git", *MODES[args.get("output_mode", "content")]]
    if args.get("case_insensitive"):
        cmd.append("-i")
    if args.get("glob"):
        cmd += ["--glob", args["glob"]]
    if args.get("context") and args.get("output_mode", "content") == "content":
        cmd += ["-C", str(int(args["context"]))]
    cmd += ["-e", args["pattern"], str(target)]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    if proc.returncode == 1:
        return "no matches"
    if proc.returncode not in (0, 1):
        raise ToolError(err.decode(errors="replace").strip() or "ripgrep failed")
    prefix = f"{target}/"
    lines = [ln.removeprefix(prefix) for ln in out.decode(errors="replace").splitlines()]
    limit = int(args.get("limit", 300))
    more = f"\n… {len(lines) - limit} more lines" if len(lines) > limit else ""
    return "\n".join(lines[:limit]) + more
