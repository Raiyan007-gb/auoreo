"""Small ops metric sample store + numpy-free trend math for the `forecast` tool (spec §2.7
predictive ops).

Samples live in `<home>/ops_metrics.db` (sync sqlite3; callers use `asyncio.to_thread`),
one row per `(metric, ts, value)`, at most `max_samples` per metric (oldest pruned). The
`ops_cpu` / `ops_mem` / `ops_disk` tools append a sample on every reading, so the
`ops_monitor` job builds the history that `forecast` extrapolates.

Metric names: `cpu.percent`, `mem.percent`, `swap.percent`, `disk.percent:<mount>`.
"""

from __future__ import annotations

import contextlib
import math
import re
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

DB_NAME = "ops_metrics.db"
METRIC_RE = re.compile(r"^[a-z][a-z0-9_.]{0,63}(:[^\x00-\x1f]{1,200})?$")
SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (metric TEXT NOT NULL, ts REAL NOT NULL, value REAL NOT NULL);
CREATE INDEX IF NOT EXISTS samples_metric_ts ON samples (metric, ts);
"""


@contextlib.contextmanager
def _db(home: Path) -> Iterator[sqlite3.Connection]:
    home.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(home / DB_NAME, timeout=5)
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def valid_metric(name: str) -> bool:
    return bool(METRIC_RE.match(name))


def record(
    home: Path, metric: str, value: float, *, ts: float | None = None, max_samples: int = 2000
) -> None:
    if not valid_metric(metric) or not math.isfinite(value):
        raise ValueError(f"bad metric sample {metric!r}={value!r}")
    with _db(home) as conn:
        conn.execute(
            "INSERT INTO samples VALUES (?, ?, ?)", (metric, ts if ts is not None else time.time(), value)
        )
        conn.execute(
            "DELETE FROM samples WHERE metric = ? AND rowid NOT IN "
            "(SELECT rowid FROM samples WHERE metric = ? ORDER BY ts DESC LIMIT ?)",
            (metric, metric, max_samples),
        )


def series(home: Path, metric: str, since: float | None = None) -> list[tuple[float, float]]:
    if not (home / DB_NAME).is_file():
        return []
    with _db(home) as conn:
        rows = conn.execute(
            "SELECT ts, value FROM samples WHERE metric = ? AND ts >= ? ORDER BY ts",
            (metric, since if since is not None else 0.0),
        ).fetchall()
    return [(float(t), float(v)) for t, v in rows]


def metrics(home: Path) -> list[tuple[str, int, float, float]]:
    """`(metric, samples, last ts, last value)` for every stored metric."""
    if not (home / DB_NAME).is_file():
        return []
    with _db(home) as conn:
        rows = conn.execute(
            "SELECT metric, COUNT(*), MAX(ts), "
            "(SELECT value FROM samples s2 WHERE s2.metric = s.metric ORDER BY ts DESC LIMIT 1) "
            "FROM samples s GROUP BY metric ORDER BY metric"
        ).fetchall()
    return [(str(m), int(n), float(t), float(v)) for m, n, t, v in rows]


# ------------------------------------------------------------------ math
@dataclass(frozen=True)
class Fit:
    slope: float  # value units per second
    intercept: float  # value at t = 0 (relative to `t0`)
    r2: float
    t0: float

    def at(self, ts: float) -> float:
        return self.intercept + self.slope * (ts - self.t0)


def linear_fit(points: list[tuple[float, float]]) -> Fit | None:
    """Ordinary least squares over `(ts, value)`; None with < 2 points or a zero time span."""
    if len(points) < 2:
        return None
    t0 = points[0][0]
    xs = [t - t0 for t, _ in points]
    ys = [v for _, v in points]
    n = len(points)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    slope = sxy / sxx
    intercept = my - slope * mx
    syy = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys, strict=True))
    r2 = 1.0 if syy == 0 else max(0.0, 1.0 - ss_res / syy)
    return Fit(slope, intercept, r2, t0)


def ewma(values: list[float], alpha: float) -> float | None:
    if not values:
        return None
    acc = values[0]
    for v in values[1:]:
        acc = alpha * v + (1 - alpha) * acc
    return acc


def time_to_threshold(fit: Fit, current: float, threshold: float, direction: str = "above") -> float | None:
    """Seconds until the trend line crosses `threshold` (`above`: rising past an upper bound,
    `below`: falling under a lower bound); 0 when already past it, None when the trend does
    not head there."""
    if direction == "below":
        if current <= threshold:
            return 0.0
        return (threshold - current) / fit.slope if fit.slope < 0 else None
    if current >= threshold:
        return 0.0
    return (threshold - current) / fit.slope if fit.slope > 0 else None


def human_duration(seconds: float) -> str:
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60)):
        if seconds >= size:
            return f"{seconds / size:.1f} {unit}"
    return f"{seconds:.0f} s"


async def record_from_tool(ctx: object, samples: dict[str, float]) -> None:
    """Fail-open hook for the ops_* tools: append their readings when enabled."""
    import asyncio
    import logging

    from auoreo.core.settings_h5 import h5_of

    cfg = h5_of(ctx).forecast
    home = getattr(ctx, "home", None)
    if not cfg.record_from_ops_tools or not isinstance(home, Path):
        return

    def write() -> None:
        now = time.time()
        for metric, value in samples.items():
            record(home, metric, value, ts=now, max_samples=cfg.max_samples)

    try:
        await asyncio.to_thread(write)
    except Exception:  # a metrics write must never fail a reading
        logging.getLogger(__name__).debug("ops metric sample not recorded", exc_info=True)
