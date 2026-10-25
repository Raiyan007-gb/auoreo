"""Chrome/Perfetto Trace-Event JSON writer (spec §20.1, §24.10; `AUOREO_TRACE_FILE`).

Driven only by the `span()` / `traced()` / `traced_stream()` / `count()` hooks in
`core/observability.py`, so it works with OpenTelemetry off and needs no hooks elsewhere.

Format: the JSON **array** form, streamed one event per line. The file opens with `[` and
metadata (`M`) events naming the process and its three tracks (`interaction`, `llm`,
`tool`); each finished span is one `X` complete event (`ts`/`dur` in µs on a monotonic
clock), approvals and compactions are `i` instants. `close()` (also run at exit) writes
`]`, so a closed file is strict JSON; a crashed one is still loadable by Perfetto, which
tolerates the missing bracket (`load()` does too).

Args are a closed allowlist per span (model, tokens, tool, decision, status …): never
prompt text, tool arguments or results.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
TRACKS: dict[str, int] = {"interaction": 1, "llm": 2, "tool": 3}
SPAN_TRACK: dict[str, str] = {"agent.run": "interaction", "llm.call": "llm", "tool.call": "tool"}
ARGS: dict[str, frozenset[str]] = {
    "agent.run": frozenset({"model", "agent", "status"}),
    "llm.call": frozenset({"model", "purpose", "tokens.prompt", "tokens.completion"}),
    "tool.call": frozenset({"tool", "decision"}),
    "approval": frozenset({"tool", "decision"}),
    "compaction": frozenset(),
}
"""Allowlisted args per event name; `error` (an exception class name) is always allowed."""
_STR_MAX = 120


def _clean(name: str, attrs: dict[str, Any] | None) -> dict[str, Any]:
    allowed = ARGS.get(name, frozenset()) | {"error"}
    out: dict[str, Any] = {}
    for k, v in (attrs or {}).items():
        if k not in allowed or v is None:
            continue
        out[k] = v if isinstance(v, (bool, int, float)) else str(v)[:_STR_MAX]
    return out


class TraceWriter:
    """One open trace file; thread-safe appends, one JSON event per line."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("w", encoding="utf-8")
        self._lock = threading.Lock()
        self._t0 = time.perf_counter_ns()
        self._pid = os.getpid()
        self._first = True
        self._fh.write("[")
        self._emit({"ph": "M", "name": "process_name", "pid": self._pid, "args": {"name": "auoreo"}})
        for track, tid in TRACKS.items():
            meta = {"ph": "M", "pid": self._pid, "tid": tid}
            self._emit({**meta, "name": "thread_name", "args": {"name": track}})
            self._emit({**meta, "name": "thread_sort_index", "args": {"sort_index": tid}})

    @property
    def closed(self) -> bool:
        return self._fh.closed

    def now_us(self) -> float:
        return (time.perf_counter_ns() - self._t0) / 1000

    def _emit(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, separators=(",", ":"), default=str)
        with self._lock:
            if self._fh.closed:
                return
            self._fh.write(("\n" if self._first else ",\n") + line)
            self._first = False
            self._fh.flush()

    def complete(self, name: str, start_us: float, attrs: dict[str, Any] | None) -> None:
        track = SPAN_TRACK.get(name, "interaction")
        self._emit(
            {
                "ph": "X",
                "name": name,
                "cat": track,
                "pid": self._pid,
                "tid": TRACKS[track],
                "ts": round(start_us, 3),
                "dur": round(max(self.now_us() - start_us, 0.0), 3),
                "args": _clean(name, attrs),
            }
        )

    def instant(self, name: str, track: str, attrs: dict[str, Any] | None = None) -> None:
        self._emit(
            {
                "ph": "i",
                "s": "t",
                "name": name,
                "cat": track,
                "pid": self._pid,
                "tid": TRACKS[track],
                "ts": round(self.now_us(), 3),
                "args": _clean(name, attrs),
            }
        )

    def close(self) -> None:
        with self._lock:
            if not self._fh.closed:
                self._fh.write("\n]\n")
                self._fh.close()


_writer: TraceWriter | None = None
_atexit = False


def configure(path: Path | str | None) -> TraceWriter | None:
    """Open (or keep) the writer for `path`; `None` closes any open one."""
    global _writer, _atexit
    target = Path(path).expanduser() if path else None
    if _writer is not None and target is not None and _writer.path == target and not _writer.closed:
        return _writer
    close()
    if target is None:
        return None
    _writer = TraceWriter(target)
    if not _atexit:
        atexit.register(close)
        _atexit = True
    return _writer


def configure_from(settings: Any) -> None:
    """`settings.trace_file` → writer; an unwritable path warns instead of failing setup."""
    try:
        configure(getattr(settings, "trace_file", None))
    except OSError as exc:
        log.warning("cannot open AUOREO_TRACE_FILE: %s", exc)


def instant(name: str, track: str, attrs: dict[str, Any] | None = None) -> None:
    if (writer := active()) is not None:
        writer.instant(name, track, attrs)


def close_on_shutdown(app: Any) -> None:
    """Close the trace file when the ASGI app's lifespan ends. Needed for servers: uvicorn
    re-raises SIGTERM/SIGINT after a graceful shutdown, so `atexit` never runs there."""
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a: Any) -> AsyncIterator[Any]:
        try:
            async with inner(a) as state:
                yield state
        finally:
            close()

    app.router.lifespan_context = lifespan


def active() -> TraceWriter | None:
    return _writer if _writer is not None and not _writer.closed else None


def close() -> None:
    global _writer
    if _writer is not None:
        _writer.close()
        _writer = None


# ------------------------------------------------------------- reading
def load(path: Path) -> list[dict[str, Any]]:
    """Events of a trace file; tolerates a missing closing `]` (crashed writer)."""
    text = path.read_text(encoding="utf-8").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = json.loads(text.rstrip(",") + "]")
    events = data.get("traceEvents", []) if isinstance(data, dict) else data
    return [e for e in events if isinstance(e, dict)]


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[idx]


def summarize(path: Path) -> list[dict[str, Any]]:
    """Per span name: count, total, p50, p95 (milliseconds), longest total first."""
    durations: dict[str, list[float]] = {}
    for e in load(path):
        if e.get("ph") == "X":
            durations.setdefault(str(e.get("name")), []).append(float(e.get("dur", 0)) / 1000)
    rows = [
        {
            "name": name,
            "count": len(ds),
            "total_ms": sum(ds),
            "p50_ms": _pct(ds, 0.5),
            "p95_ms": _pct(ds, 0.95),
        }
        for name, ds in durations.items()
    ]
    return sorted(rows, key=lambda r: -r["total_ms"])
