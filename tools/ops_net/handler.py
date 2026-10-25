"""Network counters via psutil (imported lazily)."""

import asyncio

from auoreo.tools.context import ToolContext

MAX_NICS = 20


def _mib(n: float) -> str:
    return f"{n / 2**20:.1f} MiB"


def _read() -> str:
    import psutil

    total = psutil.net_io_counters()
    lines = [
        f"**Network** sent {_mib(total.bytes_sent)} · received {_mib(total.bytes_recv)} since boot",
        "",
        "| interface | sent | received | pkts out/in | err out/in | drop out/in |",
        "|---|---|---|---|---|---|",
    ]
    per = psutil.net_io_counters(pernic=True)
    for name, c in sorted(per.items())[:MAX_NICS]:
        lines.append(
            f"| {name} | {_mib(c.bytes_sent)} | {_mib(c.bytes_recv)} | {c.packets_sent}/{c.packets_recv} "
            f"| {c.errout}/{c.errin} | {c.dropout}/{c.dropin} |"
        )
    return "\n".join(lines)


async def run(args: dict, ctx: ToolContext) -> str:
    return await asyncio.to_thread(_read)
