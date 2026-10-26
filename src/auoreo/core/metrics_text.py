"""G5c: in-process Prometheus fallback for the allowlisted metrics (spec §20.1).

`observability.count()` mirrors every allowlisted sample here (already label-filtered and
truncated) whether or not OTel is on, so `GET /metrics` works with `AUOREO_OTEL_MODE=off`
and no collector. Cardinality is bounded twice: label keys are the closed allowlist in
`observability.METRICS`, and each metric keeps at most `MAX_SERIES` label sets (further new
label sets fold into one `{overflow="true"}` series). Process-local: with `--workers N`
each worker reports its own counts.
"""

from __future__ import annotations

import threading
from collections.abc import Sized

MAX_SERIES = 100
HELP: dict[str, str] = {
    "events_published_total": "Events appended to the event store.",
    "run_completed_total": "Agent runs finished, by terminal status.",
    "tokens_total": "LLM tokens, by model and type (prompt|completion).",
    "cost_usd_total": "Estimated LLM spend in USD, by model.",
    "active_sse_connections": "Open /api/events streams.",
    "compaction_runs_total": "Context compactions run.",
    "audit_writes_total": "Audit log rows written.",
}
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
_OVERFLOW: tuple[tuple[str, str], ...] = (("overflow", "true"),)

_lock = threading.Lock()
_series: dict[str, dict[tuple[tuple[str, str], ...], float]] = {}


def add(name: str, value: float, labels: dict[str, str]) -> None:
    """Add `value` to the `name{labels}` series."""
    key = tuple(sorted(labels.items()))
    with _lock:
        series = _series.setdefault(name, {})
        if key not in series and len(series) >= MAX_SERIES:
            key = _OVERFLOW
        series[key] = series.get(key, 0.0) + value


def value(name: str, **labels: str) -> float:
    """Current value of one series (0 when unseen) — for tests and diagnostics."""
    with _lock:
        return _series.get(name, {}).get(tuple(sorted(labels.items())), 0.0)


def reset() -> None:
    with _lock:
        _series.clear()


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else repr(float(v))


WS_METRICS: dict[str, tuple[str, str, str]] = {  # --- I9: name → (type, attribute, help)
    "ws_binary_dropped_total": ("counter", "binary_dropped", "Binary /ws frames received and dropped."),
    "ws_pings_sent_total": ("counter", "pings_sent", "App-level ping{tick} frames sent on /ws."),
    "ws_keepalive_closes_total": ("counter", "keepalive_closes", "/ws closed after missed pongs."),
    "ws_connections_opened_total": ("counter", "total_opened", "/ws connections accepted."),
}


def render_ws(connections: object | None) -> str:
    """The `/ws` connection-manager counters plus the open-connection gauge (process-local)."""
    lines: list[str] = []
    for name, (kind, attr, text) in WS_METRICS.items():
        lines += [f"# HELP {name} {text}", f"# TYPE {name} {kind}"]
        lines.append(f"{name} {_num(float(getattr(connections, attr, 0) or 0))}")
    lines += ["# HELP ws_connections_active Open /ws connections.", "# TYPE ws_connections_active gauge"]
    active = len(connections) if isinstance(connections, Sized) else 0
    lines.append(f"ws_connections_active {active}")
    return "\n".join(lines) + "\n"


def render(kinds: dict[str, str]) -> str:
    """Prometheus text exposition (0.0.4) for `{name: counter|updown}`; metrics never touched
    render as a single unlabelled 0 sample so dashboards see the family from the first scrape."""
    lines: list[str] = []
    with _lock:
        snapshot = {n: dict(_series.get(n, {})) for n in kinds}
    for name, raw_kind in kinds.items():
        kind = "gauge" if raw_kind == "updown" else "counter"
        lines.append(f"# HELP {name} {HELP.get(name, name)}")
        lines.append(f"# TYPE {name} {kind}")
        for key, v in sorted(snapshot[name].items()) or [((), 0.0)]:
            label = ",".join(f'{k}="{_escape(val)}"' for k, val in key)
            lines.append(f"{name}{{{label}}} {_num(v)}" if label else f"{name} {_num(v)}")
    return "\n".join(lines) + "\n"
