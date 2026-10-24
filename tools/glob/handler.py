import asyncio
import shutil
from pathlib import Path

from auoreo.tools.context import ToolContext, ToolError


async def _rg_files(base: Path, pattern: str) -> list[Path] | None:
    rg = shutil.which("rg")
    if rg is None:
        return None
    proc = await asyncio.create_subprocess_exec(
        rg,
        "--files",
        "--hidden",
        "--glob",
        pattern,
        "--glob",
        "!.git",
        str(base),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    return [Path(p) for p in out.decode(errors="replace").splitlines() if p]


async def run(args: dict, ctx: ToolContext) -> str:
    base = ctx.resolve_path(args.get("path") or ".")
    if not base.is_dir():
        raise ToolError(f"not a directory: {base}")
    pattern = args["pattern"]
    files = await _rg_files(base, pattern)
    if files is None:
        files = [p for p in base.glob(pattern) if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    limit = int(args.get("limit", 200))
    shown = [str(p.relative_to(base)) if p.is_relative_to(base) else str(p) for p in files[:limit]]
    if not shown:
        return f"no files match {pattern!r} under {base}"
    more = f"\n… {len(files) - limit} more" if len(files) > limit else ""
    return "\n".join(shown) + more
