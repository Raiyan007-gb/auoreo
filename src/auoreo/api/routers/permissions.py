from fastapi import APIRouter, HTTPException, status

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.approvals import pending_dict, reply_with_grants
from auoreo.schema.chat import PendingPermission, PermissionReply

router = APIRouter(prefix="/api/permissions", tags=["permissions"], dependencies=AUTH)


@router.get("/pending", response_model=list[PendingPermission])
async def pending(svc: ServicesDep, session_id: str | None = None) -> list[PendingPermission]:
    return [PendingPermission(**pending_dict(p)) for p in svc.approvals.pending(session_id)]


@router.post("/{request_id}/reply")
async def reply(request_id: str, body: PermissionReply, svc: ServicesDep) -> dict[str, bool]:
    ok = await reply_with_grants(
        svc.approvals, svc.coordinator.engine_for, request_id, body.reply, body.message
    )
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no pending request with that id")
    return {"ok": True}
