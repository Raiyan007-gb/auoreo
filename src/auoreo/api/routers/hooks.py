"""Inbound webhooks: `POST /hooks/{route}` (+ `/hooks/github/{route}`). No bearer auth — the
route's signature is the auth. `GET /hooks/{route}?challenge=` answers provider verification
for routes that declare `challenge: true`."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from auoreo.api.deps import ServicesDep
from auoreo.automation.webhooks import HookResult, WebhookIngress
from auoreo.schema.chat import HookAck, HooksHealth

router = APIRouter(prefix="/hooks", tags=["hooks"])
_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {"model": HookAck, "description": "Ignored, duplicate, delivered (deliver_only) or a challenge"},
    400: {"description": "Body is not a JSON object"},
    401: {"description": "Missing/bad signature, or a missing/stale timestamp"},
    403: {"description": "Route secret not configured (fail closed)"},
    404: {"description": "Unknown or disabled route (or provider mismatch on /hooks/github/…)"},
    413: {"description": "Body over max_body_bytes"},
    415: {"description": "Not JSON"},
    429: {"description": "Route rate limit exceeded"},
    502: {"description": "deliver_only: every delivery target failed"},
}


def _ingress(svc: ServicesDep) -> WebhookIngress:
    ing = svc.ext.get("webhooks")
    return ing if isinstance(ing, WebhookIngress) else WebhookIngress(svc)


def _reply(res: HookResult) -> JSONResponse:
    return JSONResponse(res.body, status_code=res.status_code)


@router.get("/health", response_model=HooksHealth)
async def hooks_health(svc: ServicesDep) -> HooksHealth:
    return HooksHealth(routes=_ingress(svc).enabled_routes())


@router.get(
    "/{route}",
    response_class=PlainTextResponse,
    responses={404: {"description": "Unknown route"}, 405: {"description": "Not a challenge route"}},
)
async def hook_challenge(route: str, request: Request, svc: ServicesDep) -> Response:
    """Echo `?challenge=` as text/plain — only for routes with `challenge: true`."""
    res = await _ingress(svc).challenge(route, request.query_params)
    if res.status_code != 200:
        headers = {"Allow": "POST"} if res.status_code == 405 else None
        return JSONResponse(res.body, status_code=res.status_code, headers=headers)
    return PlainTextResponse(str(res.body["challenge"]), headers={"X-Content-Type-Options": "nosniff"})


@router.post("/github/{route}", status_code=202, response_model=HookAck, responses=_RESPONSES)
async def receive_github_hook(route: str, request: Request, svc: ServicesDep) -> JSONResponse:
    """Alias of `/hooks/{route}` that only matches `provider: github` routes."""
    return _reply(await _ingress(svc).receive(route, request, provider="github"))


@router.post("/{route}", status_code=202, response_model=HookAck, responses=_RESPONSES)
async def receive_hook(route: str, request: Request, svc: ServicesDep) -> JSONResponse:
    return _reply(await _ingress(svc).receive(route, request))
