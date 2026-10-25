"""Configured webhook routes (non-secret fields only)."""

from __future__ import annotations

from fastapi import APIRouter

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.automation.views import webhook_infos
from auoreo.schema.chat import WebhookRouteInfo

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"], dependencies=AUTH)


@router.get("", response_model=list[WebhookRouteInfo])
async def list_webhooks(svc: ServicesDep) -> list[WebhookRouteInfo]:
    return webhook_infos(svc)
