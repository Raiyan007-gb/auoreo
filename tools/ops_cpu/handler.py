"""CPU reading via psutil (imported lazily so discovery never needs it)."""

import asyncio
import os

from auoreo.tools.context import ToolContext
from auoreo.tools.opsmetrics import record_from_tool


def _read(interval: float) -> tuple[str, float]:
    import psutil

    per = psutil.cpu_percent(interval=interval, percpu=True)
    total = sum(per) / len(per) if per else 0.0
    lines = [
        f"**CPU** {total:.1f}% over {interval:g}s",
        f"- cores: {psutil.cpu_count(logical=False) or '?'} physical / {psutil.cpu_count() or '?'} logical",
        "- per core: " + " ".join(f"{p:.0f}%" for p in per),
    ]
    if hasattr(os, "getloadavg"):
        one, five, fifteen = os.getloadavg()
        lines.append(f"- load avg: {one:.2f} {five:.2f} {fifteen:.2f} (1/5/15 min)")
    try:
        freq = psutil.cpu_freq()
    except (OSError, NotImplementedError):
        freq = None
    if freq is not None and freq.current:
        lines.append(f"- frequency: {freq.current:.0f} MHz (max {freq.max:.0f})")
    return "\n".join(lines), total


async def run(args: dict, ctx: ToolContext) -> str:
    text, total = await asyncio.to_thread(_read, float(args.get("interval_s", 0.5)))
    await record_from_tool(ctx, {"cpu.percent": total})
    return text
