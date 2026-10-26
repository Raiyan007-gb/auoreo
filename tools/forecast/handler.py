"""forecast: linear trend + EWMA + time-to-threshold over the ops metric store (numpy-free;
math in `auoreo.tools.opsmetrics`)."""

import asyncio
import time

from auoreo.core.settings_h5 import h5_of
from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.opsmetrics import (
    ewma,
    human_duration,
    linear_fit,
    metrics,
    record,
    series,
    time_to_threshold,
    valid_metric,
)

MIN_POINTS = 3


def _sample(ctx: ToolContext, max_samples: int) -> list[str]:
    import psutil

    now = time.time()
    taken = {
        "cpu.percent": float(psutil.cpu_percent(interval=0.2)),
        "mem.percent": float(psutil.virtual_memory().percent),
        "swap.percent": float(psutil.swap_memory().percent),
    }
    for part in psutil.disk_partitions(all=False)[:15]:
        try:
            taken[f"disk.percent:{part.mountpoint}"] = float(psutil.disk_usage(part.mountpoint).percent)
        except OSError:
            continue
    for metric, value in taken.items():
        record(ctx.home, metric, value, ts=now, max_samples=max_samples)
    return sorted(taken)


def _listing(rows: list[tuple[str, int, float, float]]) -> str:
    if not rows:
        return (
            "no metrics sampled yet: run ops_cpu/ops_mem/ops_disk, enable the ops_monitor job, "
            "or pass sample_now=true"
        )
    now = time.time()
    lines = ["| metric | samples | last | age |", "|---|---|---|---|"]
    lines += [f"| {m} | {n} | {v:.2f} | {human_duration(now - t)} ago |" for m, n, t, v in rows]
    return "\n".join(lines)


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    cfg = h5_of(ctx).forecast
    if args.get("sample_now"):
        await asyncio.to_thread(_sample, ctx, cfg.max_samples)
    metric = args.get("metric")
    if args.get("list") or not metric:
        rows = await asyncio.to_thread(metrics, ctx.home)
        return ToolResult(_listing(rows), meta={"metrics": len(rows)})
    if not valid_metric(metric):
        raise ToolError(f"invalid metric name {metric!r}")
    now = time.time()
    since = now - float(args.get("window_h", 168)) * 3600
    points = await asyncio.to_thread(series, ctx.home, metric, since)
    if len(points) < MIN_POINTS:
        known = ", ".join(m for m, *_ in await asyncio.to_thread(metrics, ctx.home)) or "none"
        raise ToolError(
            f"{metric}: {len(points)} sample(s) in the window; need at least {MIN_POINTS} "
            f"(known metrics: {known}). Sample over time (ops_monitor job) or pass sample_now=true."
        )
    fit = linear_fit(points)
    values = [v for _, v in points]
    level = ewma(values, cfg.alpha) or values[-1]
    threshold = float(args.get("threshold", 90.0))
    horizon_s = float(args.get("horizon_h", 24)) * 3600
    span = points[-1][0] - points[0][0]
    lines = [
        f"**{metric}** · {len(points)} samples over {human_duration(span)}",
        f"- last {values[-1]:.2f} · EWMA(α={cfg.alpha:g}) {level:.2f}"
        f" · min {min(values):.2f} · max {max(values):.2f}",
    ]
    meta: dict = {"metric": metric, "samples": len(points), "last": values[-1], "ewma": level}
    if fit is None:
        lines.append("- trend: undefined (all samples share one timestamp)")
        return ToolResult("\n".join(lines), meta=meta)
    projected = fit.at(now + horizon_s)
    direction = str(args.get("direction", "above"))
    eta = time_to_threshold(fit, fit.at(now), threshold, direction)
    lines.append(f"- trend {fit.slope * 3600:+.3f}/h (r² {fit.r2:.2f}{', weak fit' if fit.r2 < 0.3 else ''})")
    lines.append(f"- projected in {human_duration(horizon_s)}: {projected:.2f}")
    if eta is None:
        lines.append(f"- threshold {threshold:g} ({direction}): not on course to reach it")
    elif eta == 0:
        lines.append(f"- threshold {threshold:g} ({direction}): already reached")
    else:
        lines.append(
            f"- threshold {threshold:g} ({direction}): reached in ~{human_duration(eta)} at the current trend"
        )
    meta |= {"slope_per_h": fit.slope * 3600, "r2": fit.r2, "projected": projected, "eta_s": eta}
    return ToolResult("\n".join(lines), meta=meta)
