"""OpenTelemetry tracing + allowlisted metrics (spec §20.1, §24.10, §25.15, §26.19).

The only module that imports `opentelemetry` (optional extra `otel`). `AUOREO_OTEL_MODE`:

- `off` (default): every helper is a no-op costing one attribute check.
- `local`: spans and metrics go to the console; with `AUOREO_OTEL_TEST=1` to an in-memory
  span exporter + `InMemoryMetricReader` (tests read them via `memory_exporter()` /
  `metric_reader()`).
- `otlp`: OTLP/HTTP exporters configured by the standard `OTEL_EXPORTER_OTLP_ENDPOINT` /
  `OTEL_EXPORTER_OTLP_HEADERS` env vars; spans batched, metrics exported periodically.

`Resource(service.name=auoreo)`, parent-based sampling (`OTEL_TRACES_SAMPLER_ARG` = root
ratio, default 1.0). Providers are module-owned, never installed globally, so `setup()`
can be re-run (tests) and other libraries' globals are untouched. Prompt and tool bodies
are never attached unless `otel_capture_content` is true. Metric names and labels are a
closed allowlist with bounded labels (model, type, status): never session ids or text.
If the packages are missing, `setup()` warns once and behaves as `off`.

F3: the same hooks feed the Perfetto writer (`core/tracefile.py`, works with OTel off); the
opt-in Sentry SDK (`sentry_setup`, extra `sentry`) is imported only here, always redacted.
"""

from __future__ import annotations

import importlib.util
import logging
import os
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Iterator
from contextlib import contextmanager, nullcontext, suppress
from dataclasses import dataclass, field
from typing import Any

from auoreo.core import metrics_text, tracefile

log = logging.getLogger(__name__)

SERVICE_NAME = "auoreo"
INSTALL = "uv sync --extra otel"
METRICS: dict[str, tuple[str, frozenset[str]]] = {
    "events_published_total": ("counter", frozenset()),
    "run_completed_total": ("counter", frozenset({"status"})),
    "tokens_total": ("counter", frozenset({"model", "type"})),
    "cost_usd_total": ("counter", frozenset({"model"})),
    "active_sse_connections": ("updown", frozenset()),
    "compaction_runs_total": ("counter", frozenset()),
    "audit_writes_total": ("counter", frozenset()),
}
"""Allowlist: metric → (instrument kind, permitted label keys). Anything else is dropped."""
_LABEL_MAX = 64
_CONTENT_MAX = 4000


@dataclass
class _State:
    configured: bool = False
    mode: str = "off"
    capture_content: bool = False
    warned: bool = False
    tracer: Any = None
    tracer_provider: Any = None
    meter_provider: Any = None
    instruments: dict[str, Any] = field(default_factory=dict)
    span_exporter: Any = None
    metric_reader: Any = None
    instrumented_httpx: bool = False


_state = _State()


# ------------------------------------------------------------------ setup
def setup(settings: Any) -> str:
    """Configure once from `settings.otel_mode`; returns the effective mode."""
    mode = str(getattr(settings, "otel_mode", "off") or "off")
    _side_setup(settings)
    if _state.configured and _state.mode == mode:
        return _state.mode
    if _state.configured:
        reset()
    _state.configured = True
    _state.capture_content = bool(getattr(settings, "otel_capture_content", False))
    if mode == "off":
        return "off"
    try:
        _configure(mode)
    except ImportError as exc:
        if not _state.warned:
            log.warning(
                "AUOREO_OTEL_MODE=%s but OpenTelemetry is unavailable (%s); run `%s`", mode, exc, INSTALL
            )
            _state.warned = True
        _clear()
        return "off"
    _state.mode = mode
    return mode


def _configure(mode: str) -> None:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import (
        ConsoleMetricExporter,
        InMemoryMetricReader,
        PeriodicExportingMetricReader,
    )
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
        SimpleSpanProcessor,
    )
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.sdk.trace.sampling import ParentBasedTraceIdRatio

    ratio = _ratio()
    resource = Resource.create({"service.name": SERVICE_NAME})
    tp = TracerProvider(resource=resource, sampler=ParentBasedTraceIdRatio(ratio))
    reader: Any
    if mode == "otlp":
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        reader = PeriodicExportingMetricReader(OTLPMetricExporter())
    elif os.environ.get("AUOREO_OTEL_TEST") == "1":
        _state.span_exporter = InMemorySpanExporter()
        tp.add_span_processor(SimpleSpanProcessor(_state.span_exporter))
        reader = InMemoryMetricReader()
    else:  # local: human-readable console output, flushed at exit
        tp.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        reader = PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=60_000)
    mp = MeterProvider(resource=resource, metric_readers=[reader])
    meter = mp.get_meter("auoreo")
    for name, (kind, _labels) in METRICS.items():
        make = meter.create_up_down_counter if kind == "updown" else meter.create_counter
        _state.instruments[name] = make(name)
    _state.tracer_provider, _state.meter_provider, _state.metric_reader = tp, mp, reader
    _state.tracer = tp.get_tracer("auoreo")
    _instrument_httpx()


def _instrument_httpx() -> None:
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument(tracer_provider=_state.tracer_provider)
        _state.instrumented_httpx = True
    except Exception as exc:  # instrumentation is optional
        log.info("httpx instrumentation unavailable: %s", exc)


def reset() -> None:
    """Flush and drop the providers (tests, mode changes). Safe when off."""
    if _state.instrumented_httpx:
        with suppress(Exception):
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

            HTTPXClientInstrumentor().uninstrument()
    for provider in (_state.tracer_provider, _state.meter_provider):
        if provider is not None:
            try:
                provider.shutdown()
            except Exception:
                log.debug("otel provider shutdown failed", exc_info=True)
    _clear()
    _state.configured = False


def _clear() -> None:
    warned = _state.warned
    for f in _State.__dataclass_fields__:
        setattr(_state, f, getattr(_State(), f))
    _state.warned = warned


def _ensure() -> bool:
    if not _state.configured:
        from auoreo.core.settings import get_settings

        try:
            setup(get_settings())
        except Exception:  # telemetry must never break the caller
            log.debug("otel auto-setup failed", exc_info=True)
            _state.configured = True
    return _state.tracer is not None


def enabled() -> bool:
    return _ensure()


def current_ids() -> dict[str, str]:
    """G5c log linkage: `{trace_id, span_id}` of the current span; {} when off or none."""
    if _state.tracer is None:
        return {}
    from opentelemetry import trace

    ctx = trace.get_current_span().get_span_context()
    return {"trace_id": f"{ctx.trace_id:032x}", "span_id": f"{ctx.span_id:016x}"} if ctx.is_valid else {}


def memory_exporter() -> Any:
    """The in-memory span exporter (local mode with AUOREO_OTEL_TEST=1), else None."""
    return _state.span_exporter


def metric_reader() -> Any:
    return _state.metric_reader


def instrument_app(app: Any, settings: Any) -> None:
    """FastAPI server spans (called once from create_app). No-op when off/unavailable."""
    tracefile.close_on_shutdown(app)  # uvicorn re-raises SIGTERM after shutdown: atexit never runs
    if setup(settings) == "off":
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=_state.tracer_provider,  # no meter_provider: metrics stay allowlisted
            excluded_urls="api/health,api/events",
        )
    except Exception as exc:
        log.warning("FastAPI instrumentation failed: %s", exc)


def status(settings: Any) -> tuple[str, str]:
    """Doctor row: (`ok` | `warn`, detail); Sentry and the trace file are appended when set."""
    level, detail = _otel_status(settings)
    trace = getattr(settings, "trace_file", None)
    for row in (_sentry_status(settings), ("ok", f"trace → {trace}") if trace else None):
        if row:
            level, detail = ("warn" if "warn" in (level, row[0]) else "ok"), f"{detail}; {row[1]}"
    return level, detail


def _otel_status(settings: Any) -> tuple[str, str]:
    mode = str(getattr(settings, "otel_mode", "off") or "off")
    if mode == "off":
        return "ok", "off (set AUOREO_OTEL_MODE=local|otlp to trace)"
    if importlib.util.find_spec("opentelemetry.sdk") is None:
        return "warn", f"AUOREO_OTEL_MODE={mode} but the otel extra is missing; run `{INSTALL}`"
    if mode == "otlp":
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or os.environ.get(
            "OTEL_EXPORTER_OTLP_ENDPOINT"
        )
        if not endpoint:
            return "warn", "otlp mode without OTEL_EXPORTER_OTLP_ENDPOINT (defaults to localhost:4318)"
        return "ok", f"otlp → {endpoint}"
    return "ok", "local (console exporter)"


# ------------------------------------------------------------------ spans
class Span:
    """Thin handle over an OTel span; every method is safe on the no-op instance."""

    __slots__ = ("_attrs", "_span")

    def __init__(self, span: Any = None, attrs: dict[str, Any] | None = None) -> None:
        self._span = span
        self._attrs = attrs  # trace-file args (filtered by `tracefile.ARGS` on write)

    def set(self, key: str, value: Any) -> None:
        if value is None:
            return
        if self._attrs is not None:
            self._attrs[key] = value
        if self._span is not None:
            self._span.set_attribute(key, value if isinstance(value, (bool, int, float)) else str(value))

    def content(self, key: str, text: str) -> None:
        """Attach a body only when `otel_capture_content` is on."""
        if _state.capture_content:
            self.set(key, text[:_CONTENT_MAX])

    def error(self, exc: BaseException) -> None:
        if self._attrs is not None:
            self._attrs["error"] = type(exc).__name__
        if self._span is not None:
            from opentelemetry.trace import Status, StatusCode

            self._span.record_exception(exc)
            self._span.set_status(Status(StatusCode.ERROR, type(exc).__name__))


_NOOP = Span()


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[Span]:
    """Current-context span (children nest under it); a no-op when tracing is off."""
    otel, writer = _ensure(), tracefile.active()
    if not otel and writer is None:
        yield _NOOP
        return
    start = writer.now_us() if writer is not None else 0.0
    cm = _state.tracer.start_as_current_span(name, record_exception=False) if otel else nullcontext()
    with cm as raw:
        handle = Span(raw, {} if writer is not None else None)
        for k, v in attrs.items():
            handle.set(k, v)
        try:
            yield handle
        except BaseException as exc:
            handle.error(exc)
            raise
        finally:
            if writer is not None:
                writer.complete(name, start, handle._attrs)


async def traced[T](awaitable: Awaitable[T], name: str, **attrs: Any) -> T:
    """Await `awaitable` inside a span (used around tool dispatch)."""
    if attrs.get("decision") == "ask":  # it reached execution, so a human approved it
        _instant("approval", "tool", tool=attrs.get("tool"), decision="approved")
    with span(name, **attrs):
        return await awaitable


async def traced_stream(stream: AsyncIterable[Any], *, model: str, purpose: str) -> AsyncIterator[Any]:
    """Wrap a gateway stream in an `llm.call` span; attributes come from the items' `span_attrs`
    (`Usage`: integers add up). The span is current only while the next item is awaited."""
    otel, writer = _ensure(), tracefile.active()
    if not otel and writer is None:
        async for item in stream:
            yield item
        return
    start = writer.now_us() if writer is not None else 0.0
    base = {"model": model, "purpose": purpose}
    raw = _state.tracer.start_span("llm.call", attributes=base) if otel else None
    attrs = dict(base) if writer is not None else None
    handle, it = Span(raw, attrs), aiter(stream)
    found: dict[str, Any] = {"tokens.prompt": 0, "tokens.completion": 0}
    text: list[str] = []
    try:
        while True:
            try:
                item = await _next_in(raw, it)
            except StopAsyncIteration:
                break
            for key, value in (getattr(item, "span_attrs", None) or {}).items():
                found[key] = found.get(key, 0) + value if isinstance(value, int) else value
            if _state.capture_content and isinstance(getattr(item, "text", None), str):
                text.append(item.text)
            yield item
    except BaseException as exc:
        handle.error(exc)
        raise
    finally:
        for key, value in found.items():
            handle.set(key, value)
        if text:
            handle.content("llm.completion", "".join(text))
        if raw is not None:
            raw.end()
        if writer is not None:
            writer.complete("llm.call", start, attrs)


async def _next_in(raw: Any, it: AsyncIterator[Any]) -> Any:
    """`anext(it)` with the OTel span current only for the await (never across a yield)."""
    if raw is None:
        return await anext(it)
    from opentelemetry import trace

    with trace.use_span(raw, end_on_exit=False, record_exception=False):
        return await anext(it)


# ---------------------------------------------------------------- metrics
def count(name: str, value: float = 1, **labels: Any) -> None:
    """Add to an allowlisted counter/gauge; unknown metrics and label keys are dropped."""
    if name == "compaction_runs_total":
        _instant("compaction", "interaction")
    if (spec := METRICS.get(name)) is None:
        return
    attrs = {k: str(v)[:_LABEL_MAX] for k, v in labels.items() if k in spec[1] and v is not None}
    metrics_text.add(name, value, attrs)  # G5c: `/metrics` fallback, works with OTel off
    if not _ensure() or (inst := _state.instruments.get(name)) is None:
        return
    try:
        inst.add(value, attrs)
    except Exception:  # telemetry must never break the caller
        log.debug("metric %s failed", name, exc_info=True)


def record_usage(model: str, prompt_tokens: int, completion_tokens: int, cost_usd: float | None) -> None:
    """Usage-ledger hook: `tokens_total{model,type}` + `cost_usd_total{model}`."""
    count("tokens_total", prompt_tokens, model=model, type="prompt")
    count("tokens_total", completion_tokens, model=model, type="completion")
    if cost_usd:
        count("cost_usd_total", cost_usd, model=model)


# ---------------------------------------------------------- F3: trace file
def _instant(name: str, track: str, **attrs: Any) -> None:
    _ensure()  # lazy setup also opens AUOREO_TRACE_FILE
    tracefile.instant(name, track, attrs)


def _side_setup(settings: Any) -> None:
    """Trace file + Sentry follow the settings on every `setup()` (both are idempotent)."""
    tracefile.configure_from(settings)
    sentry_setup(settings)


# -------------------------------------------------------------- F3: Sentry
SENTRY_INSTALL = "uv sync --extra sentry"
_sentry: dict[str, Any] = {"key": None, "share": "off", "active": False}


def _ratio() -> float:
    try:
        return float(os.environ.get("OTEL_TRACES_SAMPLER_ARG", "1.0"))
    except ValueError:
        return 1.0


def sentry_setup(settings: Any) -> bool:
    """Opt-in Sentry (`sentry_dsn`): lazy `sentry_sdk` import, no PII or locals, redacting
    `before_send`; `traces_sample_rate` is 0 unless `sentry_share=full` and OTel is on;
    breadcrumbs only in `full`. Idempotent per settings; returns whether it is active."""
    dsn = getattr(settings, "sentry_dsn", None)
    share = str(getattr(settings, "sentry_share", "errors_only") or "errors_only")
    otel_mode = str(getattr(settings, "otel_mode", "off") or "off")
    if _sentry["key"] == (dsn, share, otel_mode):
        return bool(_sentry["active"])
    _sentry.update(key=(dsn, share, otel_mode), share=share, active=False)
    if not dsn or share == "off":
        return False
    try:
        import sentry_sdk
    except ImportError:
        log.warning("AUOREO_SENTRY_DSN is set but sentry-sdk is missing; run `%s`", SENTRY_INSTALL)
        return False
    from auoreo import __version__

    full = share == "full"
    rate = _ratio() if full and otel_mode != "off" else 0.0
    sentry_sdk.init(
        dsn=dsn,
        release=f"auoreo@{__version__}",
        send_default_pii=False,
        include_local_variables=False,
        auto_enabling_integrations=False,  # no AI/HTTP/DB auto-integrations: they can see prompts
        max_request_body_size="never",
        max_breadcrumbs=50 if full else 0,
        traces_sample_rate=rate,
        before_send=_before_send,
        before_send_transaction=_before_send,
        before_breadcrumb=_before_breadcrumb,
        integrations=_sentry_integrations(),
    )
    _sentry["active"] = True
    return True


def _sentry_integrations() -> list[Any]:
    if importlib.util.find_spec("fastapi") is None:
        return []
    try:
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        return [StarletteIntegration(), FastApiIntegration()]
    except Exception as exc:  # DidNotEnable / version skew: run without them
        log.info("sentry FastAPI integration unavailable: %s", exc)
        return []


def _before_send(event: Any, hint: Any) -> Any:
    """Drop everything when `off`; otherwise strip bodies/PII/locals and redact secrets."""
    if _sentry["share"] == "off":
        return None
    from auoreo.core.redaction import redact_obj

    event = {k: v for k, v in event.items() if k not in {"user", "extra"}}
    if _sentry["share"] != "full":
        event.pop("breadcrumbs", None)
    if isinstance(req := event.get("request"), dict):  # method + URL only: no body/headers/cookies
        event["request"] = {k: req[k] for k in ("method", "url") if k in req}
    for exc in (event.get("exception") or {}).get("values") or []:
        for frame in (exc.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)
    return redact_obj(event)


def _before_breadcrumb(crumb: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    from auoreo.core.redaction import redact_obj  # breadcrumbs only in `full`, never HTTP data

    return redact_obj({k: v for k, v in crumb.items() if k != "data"}) if _sentry["share"] == "full" else None


def _sentry_status(settings: Any) -> tuple[str, str] | None:
    if not getattr(settings, "sentry_dsn", None):
        return None
    if (share := settings.sentry_share) != "off" and importlib.util.find_spec("sentry_sdk") is None:
        return "warn", f"sentry: DSN set but sentry-sdk is missing; run `{SENTRY_INSTALL}`"
    return "ok", f"sentry: {share} (redacted, no PII)"
