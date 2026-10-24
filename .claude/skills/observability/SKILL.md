---
name: observability
description: Instrument the platform with OpenTelemetry Python — traces, spans, attributes, OTLP export, FastAPI auto-instrumentation; structured logs. Use when building agent_core/observability in this repo.
---

# OpenTelemetry Python — observability (plan.md Part H #15, Part I Phase 5)

Docs: https://opentelemetry.io/docs/languages/python · Context7: `/open-telemetry/opentelemetry-python`.

## Setup

```bash
uv add opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
uv add --dev opentelemetry-instrumentation-fastapi opentelemetry-instrumentation-aiohttp-client
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
trace.get_tracer_provider().add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint="http://localhost:4318/v1/traces")))

tracer = trace.get_tracer(__name__)
with tracer.start_as_current_span("agent.run", attributes={"session_id": sess_id, "model": model_ref}):
    # ... agent runtime work; nested spans for tool calls / LLM calls
```

- Attributes are the searchable metadata (session_id, run_id, tool_name, model, token counts). Add them at span start.
- Async context propagates automatically (no manual parent passing) — an `await` inside a span keeps the trace.
- Correlation: attach `session_id`/`run_id` to every span so per-session latency/cost analysis works.
- The durable event stream (see `sse-events` skill) doubles as the audit trail; spans add the low-level timing/trace context on top.

## FastAPI auto-instrumentation

```python
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
FastAPIInstrumentor.instrument_app(app)   # wraps routes → one span per HTTP request

# OTLP exporter running alongside; Grafana/Prometheus visualize via Tempo/Prometheus backends
```

Instrument aiohttp/httpx clients too so provider calls (LiteLLM → model API) appear in the distributed trace.

## Structured logs

- Use `structlog` (or stdlib `logging` with JSON formatter) with keys: `session_id`, `run_id`, `event_type`, `ts`.
- Inject the current span context into log records via **`opentelemetry-instrumentation-logging`'s `LoggingHandler`** so logs link to traces — the `_translate` pattern adds `trace_id`/`span_id` automatically. (The `LoggingHandler` in `opentelemetry-sdk` itself is **deprecated**; use the contrib package.)
- Never log secrets, prompts bodies by default (opt-in), or raw API keys (plan.md Part K). Log counts/IDs, not content, unless debug.

## Metrics (Phase 5)

- Prometheus-format counters: `events_published_total`, `active_sse_connections`, `run_completed_total`, `tokens_total{model=}`. Gauge for active SSE connections catches memory leaks (from Exa SSE guide).
- **Current 2026 path:** reuse the OTLP HTTP exporter and point it at Prometheus' **native OTLP receiver** (Prometheus ≥ 2.47 with `--web.enable-otlp-receiver`) —
  `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=http://localhost:9090/api/v1/otlp`. The dedicated
  `opentelemetry-exporter-prometheus` is superseded for this (it also lacks multiprocessing support);
  a plain `/metrics` endpoint (prometheus-client) is still fine if you don't want OTLP.

## Repo boundaries

- `agent_core/observability.py` centralizes provider setup + span helpers (`start_span("agent.run", ...)`).
- Handlers/emitters call those helpers; don't scatter tracer creation across packages.
- Keep instrumentation **off by default in tests** (env-gated) so unit tests stay fast/hermetic; integration/e2e enable it against an OTLP mock.