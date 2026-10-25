"""Inbound webhooks: `POST /hooks/{route}`. No bearer auth — the route's HMAC is the auth."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from auoreo.api.deps import ServicesDep
from auoreo.automation.webhooks import WebhookIngress
from auoreo.schema.chat import HookAck, HooksHealth

router = APIRouter(prefix="/hooks", tags=["hooks"])


def _ingress(svc: ServicesDep) -> WebhookIngress:
    ing = svc.ext.get("webhooks")
    return ing if isinstance(ing, WebhookIngress) else WebhookIngress(svc)


@router.get("/health", response_model=HooksHealth)
async def hooks_health(svc: ServicesDep) -> HooksHealth:
    return HooksHealth(routes=_ingress(svc).enabled_routes())


@router.post(
    "/{route}",
    status_code=202,
    response_model=HookAck,
    responses={
        200: {"model": HookAck, "description": "Ignored by event/filter, or a duplicate delivery id"},
        401: {"description": "Missing or bad signature"},
        404: {"description": "Unknown or disabled route"},
        413: {"description": "Body over max_body_bytes"},
        415: {"description": "Not JSON"},
        429: {"description": "Route rate limit exceeded"},
        503: {"description": "Route secret not configured (fail closed)"},
    },
)
async def receive_hook(route: str, request: Request, svc: ServicesDep) -> JSONResponse:
    res = await _ingress(svc).receive(route, request)
    return JSONResponse(res.body, status_code=res.status_code)
