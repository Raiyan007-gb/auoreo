import asyncio
import re
import shutil
from pathlib import Path

from auoreo.core.settings_h5 import h5_of
from auoreo.tools.context import ToolContext, ToolError
from auoreo.tools.pysearch import SearchOptions, search

MODES = {"content": ["-n", "--no-heading"], "files": ["-l"], "count": ["-c"]}


async def _rg(rg: str, args: dict, target: str) -> list[str] | None:
    cmd = [rg, "--color=never", "--hidden", "--glob", "!.git", *MODES[args.get("output_mode", "content")]]
    if args.get("case_insensitive"):
        cmd.append("-i")
    if args.get("glob"):
        cmd += ["--glob", args["glob"]]
    if args.get("context") and args.get("output_mode", "content") == "content":
        cmd += ["-C", str(int(args["context"]))]
    cmd += ["-e", args["pattern"], target]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    if proc.returncode == 1:
        return None
    if proc.returncode not in (0, 1):
        raise ToolError(err.decode(errors="replace").strip() or "ripgrep failed")
    return out.decode(errors="replace").splitlines()


async def _python(args: dict, ctx: ToolContext, target: Path) -> list[str] | None:
    cfg = h5_of(ctx).grep
    opts = SearchOptions(
        pattern=args["pattern"],
        mode=args.get("output_mode", "content"),
        case_insensitive=bool(args.get("case_insensitive")),
        glob=args.get("glob"),
        context=int(args.get("context") or 0),
        max_file_bytes=cfg.max_file_bytes,
        max_files=cfg.max_files,
        max_lines=int(args.get("limit", 300)) * 4 + 100,
    )
    try:
        lines = await asyncio.to_thread(search, target, opts)
    except re.error as exc:
        raise ToolError(f"invalid regex: {exc}") from exc
    return lines or None


async def run(args: dict, ctx: ToolContext) -> str:
    target = ctx.resolve_path(args.get("path") or ".")
    if not target.exists():
        raise ToolError(f"path does not exist: {target}")
    rg = shutil.which("rg")
    note = ""
    if rg is not None:
        found = await _rg(rg, args, str(target))
    elif h5_of(ctx).grep.python_fallback:
        found = await _python(args, ctx, target)
        note = "\n(ripgrep not installed: searched with the built-in Python fallback)"
    else:
        raise ToolError("ripgrep (rg) is not installed and h5.grep.python_fallback is off")
    if found is None:
        return "no matches" + note
    prefix = f"{target}/"
    lines = [ln.removeprefix(prefix) for ln in found]
    limit = int(args.get("limit", 300))
    more = f"\n… {len(lines) - limit} more lines" if len(lines) > limit else ""
    return "\n".join(lines[:limit]) + more + note
