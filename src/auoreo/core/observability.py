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
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

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

    try:
        ratio = float(os.environ.get("OTEL_TRACES_SAMPLER_ARG", "1.0"))
    except ValueError:
        ratio = 1.0
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
        try:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

            HTTPXClientInstrumentor().uninstrument()
        except Exception:
            pass
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


def memory_exporter() -> Any:
    """The in-memory span exporter (local mode with AUOREO_OTEL_TEST=1), else None."""
    return _state.span_exporter


def metric_reader() -> Any:
    return _state.metric_reader


def instrument_app(app: Any, settings: Any) -> None:
    """FastAPI server spans (called once from create_app). No-op when off/unavailable."""
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
    """Doctor row: (`ok` | `warn`, detail)."""
    import importlib.util

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

    __slots__ = ("_span",)

    def __init__(self, span: Any = None) -> None:
        self._span = span

    def set(self, key: str, value: Any) -> None:
        if self._span is not None and value is not None:
            self._span.set_attribute(key, value if isinstance(value, (bool, int, float)) else str(value))

    def content(self, key: str, text: str) -> None:
        """Attach a body only when `otel_capture_content` is on."""
        if _state.capture_content:
            self.set(key, text[:_CONTENT_MAX])

    def error(self, exc: BaseException) -> None:
        if self._span is not None:
            from opentelemetry.trace import Status, StatusCode

            self._span.record_exception(exc)
            self._span.set_status(Status(StatusCode.ERROR, type(exc).__name__))


_NOOP = Span()


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[Span]:
    """Current-context span (children nest under it); a no-op when tracing is off."""
    if not _ensure():
        yield _NOOP
        return
    with _state.tracer.start_as_current_span(name, record_exception=False) as raw:
        handle = Span(raw)
        for k, v in attrs.items():
            handle.set(k, v)
        try:
            yield handle
        except BaseException as exc:
            handle.error(exc)
            raise


async def traced[T](awaitable: Awaitable[T], name: str, **attrs: Any) -> T:
    """Await `awaitable` inside a span (used around tool dispatch)."""
    with span(name, **attrs):
        return await awaitable


async def traced_stream(stream: AsyncIterable[Any], *, model: str, purpose: str) -> AsyncIterator[Any]:
    """Wrap a gateway stream in an `llm.call` span; token counts come from `Usage` items.

    The span is made current only while the next item is awaited, never across a `yield`,
    so the caller's context is untouched between items.
    """
    if not _ensure():
        async for item in stream:
            yield item
        return
    from opentelemetry import trace

    raw = _state.tracer.start_span("llm.call", attributes={"model": model, "purpose": purpose})
    handle, it = Span(raw), aiter(stream)
    prompt = completion = 0
    text: list[str] = []
    try:
        while True:
            with trace.use_span(raw, end_on_exit=False, record_exception=False):
                try:
                    item = await anext(it)
                except StopAsyncIteration:
                    break
            prompt += int(getattr(item, "prompt_tokens", 0) or 0)
            completion += int(getattr(item, "completion_tokens", 0) or 0)
            if _state.capture_content and isinstance(getattr(item, "text", None), str):
                text.append(item.text)
            yield item
    except BaseException as exc:
        handle.error(exc)
        raise
    finally:
        handle.set("tokens.prompt", prompt)
        handle.set("tokens.completion", completion)
        if text:
            handle.content("llm.completion", "".join(text))
        raw.end()


# ---------------------------------------------------------------- metrics
def count(name: str, value: float = 1, **labels: Any) -> None:
    """Add to an allowlisted counter/gauge; unknown metrics and label keys are dropped."""
    if not _ensure():
        return
    inst = _state.instruments.get(name)
    if inst is None:
        return
    allowed = METRICS[name][1]
    attrs = {k: str(v)[:_LABEL_MAX] for k, v in labels.items() if k in allowed and v is not None}
    try:
        inst.add(value, attrs)
    except Exception:  # telemetry must never break the caller
        log.debug("metric %s failed", name, exc_info=True)


def record_usage(model: str, prompt_tokens: int, completion_tokens: int, cost_usd: float | None) -> None:
    """Usage-ledger hook: `tokens_total{model,type}` + `cost_usd_total{model}`."""
    if not _ensure():
        return
    count("tokens_total", prompt_tokens, model=model, type="prompt")
    count("tokens_total", completion_tokens, model=model, type="completion")
    if cost_usd:
        count("cost_usd_total", cost_usd, model=model)
