from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.approvals import grouped, pending_dict, reply_with_grants
from auoreo.schema.chat import PendingPermission, PermissionReply

router = APIRouter(prefix="/api/permissions", tags=["permissions"], dependencies=AUTH)


@router.get("/pending", response_model=list[PendingPermission])
async def pending(request: Request, svc: ServicesDep, session_id: str | None = None) -> Any:
    # `?group_by=permission` (spec §25.10) returns collapsible groups keyed by
    # (permission, cwd_root, risk_tier, tool). It is read from the raw query so the
    # published OpenAPI contract is unchanged until the UI adopts it.
    items = svc.approvals.pending(session_id)
    group_by = request.query_params.get("group_by")
    if group_by is not None:
        if group_by != "permission":
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "group_by must be 'permission'")
        return JSONResponse(grouped(items))
    return [PendingPermission(**pending_dict(p)) for p in items]


@router.post("/{request_id}/reply")
async def reply(request_id: str, body: PermissionReply, svc: ServicesDep) -> dict[str, bool]:
    ok = await reply_with_grants(
        svc.approvals, svc.coordinator.engine_for, request_id, body.reply, body.message, body.cascade
    )
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no pending request with that id")
    return {"ok": True}
