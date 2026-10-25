"""G5c: `GET /metrics` — Prometheus text for the allowlisted metrics (spec §20.1).

The scrape fallback when no OTLP collector is configured (it is served in every
`AUOREO_OTEL_MODE`; with `otlp` the same counters are also exported). Behind the same
bearer/cookie auth as `/api/*`: point the scraper at it with `authorization: Bearer <token>`.
"""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from auoreo.api.deps import AUTH
from auoreo.core import metrics_text
from auoreo.core import observability as obs

router = APIRouter(tags=["metrics"], dependencies=AUTH)


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    responses={200: {"content": {"text/plain": {"schema": {"type": "string"}}}}},
)
async def metrics() -> PlainTextResponse:
    """Prometheus text exposition 0.0.4 (process-local counters; bounded labels)."""
    body = metrics_text.render({name: kind for name, (kind, _labels) in obs.METRICS.items()})
    return PlainTextResponse(body, media_type=metrics_text.CONTENT_TYPE)
