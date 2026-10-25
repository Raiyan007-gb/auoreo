"""Disk reading via psutil (imported lazily)."""

import asyncio
from pathlib import Path

from auoreo.tools.context import ToolContext, ToolError

MAX_PARTITIONS = 15


def _gib(n: float) -> str:
    return f"{n / 2**30:.1f} GiB"


def _read(path: Path) -> str:
    import psutil

    du = psutil.disk_usage(str(path))
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
    return "\n".join(lines)


async def run(args: dict, ctx: ToolContext) -> str:
    path = ctx.resolve_path(args.get("path") or ".")
    if not path.exists():
        raise ToolError(f"path does not exist: {path}")
    return await asyncio.to_thread(_read, path)
