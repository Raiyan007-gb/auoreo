"""Top processes via psutil (imported lazily). CPU % needs two samples, so the first
pass primes the counters and the second (after a short sleep) reads them."""

import asyncio
import time

from auoreo.tools.context import ToolContext

ATTRS = ["pid", "name", "username", "memory_percent", "memory_info", "cmdline"]
SAMPLE_S = 0.3


def _read(limit: int, sort: str, needle: str) -> str:
    import psutil

    procs = []
    for p in psutil.process_iter(ATTRS, ad_value=None):
        info = p.info
        hay = f"{info.get('name') or ''} {' '.join(info.get('cmdline') or [])}".lower()
        if needle and needle not in hay:
            continue
        try:
            p.cpu_percent(None)  # prime
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        procs.append(p)
    time.sleep(SAMPLE_S)
    rows = []
    for p in procs:
        try:
            cpu = p.cpu_percent(None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        info = p.info
        mem = info.get("memory_percent") or 0.0
        rss = getattr(info.get("memory_info"), "rss", 0) or 0
        rows.append((cpu, mem, rss, info))
    rows.sort(key=lambda r: r[0] if sort == "cpu" else r[1], reverse=True)
    head = f"**Processes** {len(rows)} matched, top {min(limit, len(rows))} by {sort}"
    lines = [head, "", "| pid | user | cpu % | mem % | rss | name |", "|---|---|---|---|---|---|"]
    for cpu, mem, rss, info in rows[:limit]:
        name = str(info.get("name") or "?").replace("|", "/")
        user = str(info.get("username") or "?")
        lines.append(f"| {info['pid']} | {user} | {cpu:.1f} | {mem:.1f} | {rss / 2**20:.0f} MiB | {name} |")
    return "\n".join(lines)


async def run(args: dict, ctx: ToolContext) -> str:
    limit = int(args.get("limit", 15))
    sort = args.get("sort", "cpu")
    needle = str(args.get("filter") or "").lower()
    return await asyncio.to_thread(_read, limit, sort, needle)
