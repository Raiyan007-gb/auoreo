---
name: observability
description: Instrument the platform with OpenTelemetry Python — traces, spans, attributes, OTLP export, FastAPI auto-instrumentation; structured logs. Use when building agent_core/observability in this repo.
---

# OpenTelemetry Python — observability (plan.md Part H #15, Part I Phase 5)

Docs: https://opentelemetry.io/docs/languages/python · Context7: `/open-telemetry/opentelemetry-python` (SDK, v1.42.x), `/open-telemetry/opentelemetry-python-contrib` (instrumentations), `/websites/prometheus_io` (OTLP receiver).

## Setup

```bash
uv add opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
# instrumentations run in prod, so they are runtime deps (not --dev):
uv add opentelemetry-instrumentation-fastapi opentelemetry-instrumentation-httpx \
       opentelemetry-instrumentation-aiohttp-client opentelemetry-instrumentation-logging
```

## Basic tracing

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource

resource = Resource.create(attributes={"service.name": "agent-platform"})
trace.set_tracer_provider(TracerProvider(resource=resource))
# endpoint kwarg is used verbatim (include /v1/traces); omit it to use OTEL_EXPORTER_OTLP_ENDPOINT (+ /v1/traces) or localhost:4318
trace.get_tracer_provider().add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint="http://localhost:4318/v1/traces"))
)

tracer = trace.get_tracer(__name__)
with tracer.start_as_current_span("agent.run", attributes={"session_id": sess_id, "model": model_ref}):
    ...  # agent runtime work; nested spans for tool calls / LLM calls
```

- Attributes are the searchable metadata (session_id, run_id, tool_name, model, token counts). Add them at span start.
- Async context propagates automatically (no manual parent passing) — an `await` inside a span keeps the trace.
- Correlation: attach `session_id`/`run_id` to every span so per-session latency/cost analysis works.
- The durable event stream (see `sse-events` skill) doubles as the audit trail; spans add the low-level timing/trace context on top.

## FastAPI auto-instrumentation

```python
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

FastAPIInstrumentor.instrument_app(
    app, excluded_urls="healthz,api/event"
)  # one span per request; skip health + long-lived SSE

# OTLP exporter running alongside; Grafana/Prometheus visualize via Tempo/Prometheus backends
```

Instrument httpx/aiohttp clients too (`HTTPXClientInstrumentor().instrument()`, `AioHttpClientInstrumentor().instrument()`) so provider calls (LiteLLM → model API) appear in the distributed trace. `excluded_urls` also reads `OTEL_PYTHON_FASTAPI_EXCLUDED_URLS` / `OTEL_PYTHON_EXCLUDED_URLS`.

## Structured logs

- Use `structlog` (or stdlib `logging` with JSON formatter) with keys: `session_id`, `run_id`, `event_type`, `ts`.
- Link logs to traces with **`opentelemetry-instrumentation-logging`**: `LoggingInstrumentor().instrument(inject_trace_context=True)` stamps every stdlib `LogRecord` with `otelTraceID`/`otelSpanID`/`otelTraceSampled`/`otelServiceName` (map them into the JSON keys; env alternative `OTEL_PYTHON_LOG_CORRELATION=true`). Injection is opt-in — nothing is added unless configured.
- To ship logs over OTLP, bridge stdlib logging with `from opentelemetry.instrumentation.logging.handler import LoggingHandler` attached to an SDK `LoggerProvider` (+ `BatchLogRecordProcessor(OTLPLogExporter())`, `set_logger_provider`); it carries the active span context on each record. The `LoggingHandler` in `opentelemetry-sdk` (and `OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED`) is **deprecated** — use the contrib package.
- Never log secrets, prompts bodies by default (opt-in), or raw API keys (plan.md Part K). Log counts/IDs, not content, unless debug.

## Metrics (Phase 5)

- Prometheus-format counters: `events_published_total`, `active_sse_connections`, `run_completed_total`, `tokens_total{model=}`. Gauge for active SSE connections catches memory leaks (from Exa SSE guide).
- **Current 2026 path:** reuse the OTLP HTTP exporter and point it at Prometheus' **native OTLP receiver** (receiver new in v2.47; start Prometheus with `--web.enable-otlp-receiver`, serves `/api/v1/otlp/v1/metrics`) —
  `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=http://localhost:9090/api/v1/otlp/v1/metrics` (the Python exporter uses signal-specific endpoints **verbatim** — no `/v1/metrics` is appended; only the generic `OTEL_EXPORTER_OTLP_ENDPOINT` gets the suffix). Prometheus docs call this suitable for low-volume use, not a scrape replacement. The dedicated
  `opentelemetry-exporter-prometheus` is superseded for this (it also lacks multiprocessing support);
  a plain `/metrics` endpoint (prometheus-client) is still fine if you don't want OTLP.

## Repo boundaries

- `agent_core/observability.py` centralizes provider setup + span helpers (`start_span("agent.run", ...)`).
- Handlers/emitters call those helpers; don't scatter tracer creation across packages.
- Keep instrumentation **off by default in tests** (env-gated) so unit tests stay fast/hermetic; integration/e2e enable it against an OTLP mock.