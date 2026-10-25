"""Ops endpoints (W0-B): `GET /api/doctor`, `GET /api/audit`, `GET /api/audit/verify`,
`POST /api/welcome/event` (onboarding steps are audited).

Doctor never repairs, audit rows are redacted on the way out; only the welcome event writes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core import diagnostics
from auoreo.core.db import AuditRow
from auoreo.core.redaction import redact, redact_obj
from auoreo.schema.ops import (
    AuditEntry,
    AuditPage,
    AuditVerify,
    DoctorCheck,
    DoctorReport,
    DoctorSummary,
    WelcomeEvent,
)

router = APIRouter(prefix="/api", tags=["ops"], dependencies=AUTH)


@router.get("/doctor", response_model=DoctorReport)
async def doctor(svc: ServicesDep) -> DoctorReport:
    """The `auoreo doctor` checks against the live server (no repairs, no secret values)."""
    checks = await diagnostics.run_checks(svc.settings, services=svc)
    return DoctorReport(
        checks=[DoctorCheck(**c.as_dict()) for c in checks],
        summary=DoctorSummary(**diagnostics.summarize(checks)),
    )


def _entry(row: AuditRow) -> AuditEntry:
    return AuditEntry(
        seq=row.seq,
        ts=row.ts,
        session_id=row.session_id,
        actor=row.actor,
        action=row.action,
        decision=row.decision,
        reason=redact(row.reason),
        detail=redact_obj(row.detail or {}),
        prev_hash=row.prev_hash,
        row_hash=row.row_hash,
    )


@router.get("/audit", response_model=AuditPage)
async def audit_list(
    svc: ServicesDep,
    after_seq: Annotated[int | None, Query(ge=0)] = None,
    before_seq: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    session_id: str | None = None,
    action: str | None = None,
    newest_first: bool = False,
) -> AuditPage:
    """Audit rows in seq order (`newest_first` reverses it). Filters combine with AND."""
    q = select(AuditRow)
    if after_seq is not None:
        q = q.where(AuditRow.seq > after_seq)
    if before_seq is not None:
        q = q.where(AuditRow.seq < before_seq)
    if session_id:
        q = q.where(AuditRow.session_id == session_id)
    if action:
        q = q.where(AuditRow.action == action)
    q = q.order_by(AuditRow.seq.desc() if newest_first else AuditRow.seq).limit(limit + 1)
    async with svc.db.session() as s:
        rows = list((await s.execute(q)).scalars())
    more = len(rows) > limit
    rows = rows[:limit]
    nxt = rows[-1].seq if more and rows and not newest_first else None
    return AuditPage(rows=[_entry(r) for r in rows], next_after_seq=nxt)


@router.get("/audit/verify", response_model=AuditVerify)
async def audit_verify(svc: ServicesDep) -> AuditVerify:
    """Recompute the SHA-256 hash chain; `first_break_seq` is the first row that fails."""
    broken = await svc.audit.verify()
    async with svc.db.session() as s:
        total = int((await s.execute(select(func.count()).select_from(AuditRow))).scalar_one())
    return AuditVerify(ok=broken is None, first_break_seq=broken, rows=total)


@router.post("/welcome/event", status_code=status.HTTP_204_NO_CONTENT)
async def welcome_event(body: WelcomeEvent, svc: ServicesDep) -> None:
    """Record an onboarding step (completed / skipped / dismissed) in the audit log."""
    await svc.audit.record(actor="user", action=f"welcome.{body.action}", detail={"step": body.step})
