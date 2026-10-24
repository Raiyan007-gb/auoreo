from fastapi import APIRouter, HTTPException, status

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.approvals import Pending
from auoreo.schema.chat import PendingPermission, PermissionReply

router = APIRouter(prefix="/api/permissions", tags=["permissions"], dependencies=AUTH)


@router.get("/pending", response_model=list[PendingPermission])
async def pending(svc: ServicesDep, session_id: str | None = None) -> list[PendingPermission]:
    return [
        PendingPermission(
            request_id=p.request_id,
            session_id=p.session_id,
            tool=p.tool,
            permission=p.permission,
            pattern=p.pattern,
            detail=p.detail,
        )
        for p in svc.approvals.pending(session_id)
    ]


@router.post("/{request_id}/reply")
async def reply(request_id: str, body: PermissionReply, svc: ServicesDep) -> dict[str, bool]:
    def grant_and_scan(granted: Pending) -> list[str]:
        """Persist the `always` grant, then auto-resolve pendings it now allows."""
        engine = svc.coordinator.engine_for(granted.session_id)
        if engine is None:
            return []
        engine.grant_always(granted.permission, granted.pattern)
        return [
            p.request_id
            for p in svc.approvals.pending(granted.session_id)
            if engine.evaluate(p.permission, p.pattern).action == "allow"
        ]

    ok = await svc.approvals.reply(
        request_id,
        body.reply,
        body.message,
        on_always=grant_and_scan if body.reply == "always" else None,
    )
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no pending request with that id")
    return {"ok": True}
