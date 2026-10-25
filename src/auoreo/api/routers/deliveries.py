"""Deliveries outbox: list and replay."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.automation.deliveries import outbox_of
from auoreo.automation.views import delivery_info
from auoreo.schema.chat import DeliveryInfo, DeliveryReplay

router = APIRouter(prefix="/api/deliveries", tags=["deliveries"], dependencies=AUTH)


@router.get("", response_model=list[DeliveryInfo])
async def list_deliveries(
    svc: ServicesDep, status: str | None = None, kind: str | None = None, limit: int = 50
) -> list[DeliveryInfo]:
    return [delivery_info(r) for r in await outbox_of(svc).list(status, kind, limit)]


NOT_REPLAYABLE = "suggestion"
"""Plain-text suggestion rows carry no agent prompt; replaying would run an empty turn."""


@router.post("/{delivery_id}/replay", response_model=DeliveryReplay)
async def replay_delivery(delivery_id: int, svc: ServicesDep, dry_run: bool = True) -> DeliveryReplay:
    """`dry_run=true` (default) only shows the stored prompt; otherwise a new pending row
    with the same payload is queued (the original is untouched)."""
    outbox = outbox_of(svc)
    row = await outbox.get(delivery_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "delivery not found")
    prompt = str(row.payload.get("prompt") or "")
    if dry_run:
        return DeliveryReplay(dry_run=True, prompt=prompt)
    if row.kind == NOT_REPLAYABLE:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "suggestion deliveries are not replayable; propose it again"
        )
    ext = f"replay-{row.id}-{uuid.uuid4().hex[:8]}"
    new = await outbox.enqueue(row.kind, row.source, ext, dict(row.payload), row.target)
    if new is None:  # pragma: no cover - random suffix
        raise HTTPException(status.HTTP_409_CONFLICT, "replay collided; retry")
    await svc.audit.record(
        actor="api", action="delivery.replay", decision="queued", detail={"from": row.id, "to": new.id}
    )
    return DeliveryReplay(dry_run=False, prompt=prompt, delivery=delivery_info(new))
