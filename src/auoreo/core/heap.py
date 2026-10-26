"""I5: `auoreo debug snapshot --heap` — tracemalloc top allocation sites, on demand only
(spec §20.3). Tracing is started for the measured workload and stopped right after; nothing
is left enabled. File paths are made relative to the package or site-packages so the report
holds no home-directory paths."""

from __future__ import annotations

import sys
import tracemalloc
from collections.abc import Callable
from pathlib import Path
from typing import Any

FRAMES = 10


def _short(filename: str) -> str:
    p = Path(filename)
    for marker in ("site-packages", "auoreo"):
        if marker in p.parts:
            return "/".join(p.parts[p.parts.index(marker) + (1 if marker == "site-packages" else 0) :])
    return p.name


def heap_snapshot(workload: Callable[[], Any], top: int = 25) -> dict[str, Any]:
    """Run `workload` under tracemalloc; the `top` allocation sites (by size) still held."""
    was_tracing = tracemalloc.is_tracing()
    if not was_tracing:
        tracemalloc.start(FRAMES)
    try:
        workload()
        snap = tracemalloc.take_snapshot()
        current, peak = tracemalloc.get_traced_memory()
    finally:
        if not was_tracing:
            tracemalloc.stop()
    snap = snap.filter_traces((tracemalloc.Filter(False, tracemalloc.__file__),))
    stats = snap.statistics("lineno")
    return {
        "python": sys.version.split()[0],
        "current_bytes": current,
        "peak_bytes": peak,
        "sites": len(stats),
        "top": [
            {
                "where": f"{_short(s.traceback[0].filename)}:{s.traceback[0].lineno}",
                "size_bytes": s.size,
                "count": s.count,
            }
            for s in stats[: max(1, top)]
        ],
    }
