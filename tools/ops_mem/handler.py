"""Memory reading via psutil (imported lazily)."""

import asyncio

from auoreo.tools.context import ToolContext
from auoreo.tools.opsmetrics import record_from_tool


def _gib(n: float) -> str:
    return f"{n / 2**30:.2f} GiB"


def _read() -> tuple[str, dict[str, float]]:
    import psutil

    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    return "\n".join(
        [
            f"**Memory** {vm.percent:.1f}% used",
            f"- total {_gib(vm.total)} · used {_gib(vm.used)} · available {_gib(vm.available)}",
            f"**Swap** {sw.percent:.1f}% used",
            f"- total {_gib(sw.total)} · used {_gib(sw.used)} · free {_gib(sw.free)}",
        ]
    ), {"mem.percent": float(vm.percent), "swap.percent": float(sw.percent)}


async def run(args: dict, ctx: ToolContext) -> str:
    text, samples = await asyncio.to_thread(_read)
    await record_from_tool(ctx, samples)
    return text
