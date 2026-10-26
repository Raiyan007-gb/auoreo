"""Disk reading via psutil (imported lazily)."""

import asyncio
from pathlib import Path

from auoreo.tools.context import ToolContext, ToolError
from auoreo.tools.opsmetrics import record_from_tool

MAX_PARTITIONS = 15


def _gib(n: float) -> str:
    return f"{n / 2**30:.1f} GiB"


def _read(path: Path) -> tuple[str, dict[str, float]]:
    import psutil

    du = psutil.disk_usage(str(path))
    samples = {f"disk.percent:{_mount(path)}": float(du.percent)}
    lines = [
        f"**Disk** `{path}` {du.percent:.1f}% used",
        f"- total {_gib(du.total)} · used {_gib(du.used)} · free {_gib(du.free)}",
        "",
        "| mount | fs | used | free |",
        "|---|---|---|---|",
    ]
    for part in psutil.disk_partitions(all=False)[:MAX_PARTITIONS]:
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        lines.append(f"| {part.mountpoint} | {part.fstype} | {u.percent:.0f}% | {_gib(u.free)} |")
        samples[f"disk.percent:{part.mountpoint}"] = float(u.percent)
    return "\n".join(lines), samples


def _mount(path: Path) -> str:
    """The mount point holding `path` (so samples key on mounts, not arbitrary dirs)."""
    cur = path.resolve()
    while not cur.is_mount() and cur.parent != cur:
        cur = cur.parent
    return str(cur)


async def run(args: dict, ctx: ToolContext) -> str:
    path = ctx.resolve_path(args.get("path") or ".")
    if not path.exists():
        raise ToolError(f"path does not exist: {path}")
    text, samples = await asyncio.to_thread(_read, path)
    await record_from_tool(ctx, samples)
    return text
