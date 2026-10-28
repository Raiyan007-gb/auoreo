"""Ops endpoints (W0-B): `GET /api/doctor`, `GET|POST /api/doctor/fixes`, `GET /api/audit`,
`GET /api/audit/verify`, `POST /api/welcome/event` (onboarding steps are audited).

Doctor never repairs; its safe fixes are a separate, explicit POST (the CLI's `--fix`), each
applied fix audited. Audit rows are redacted on the way out."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core import diagnostics, doctor_fix
from auoreo.core.db import AuditRow
from auoreo.core.redaction import redact
from auoreo.core.services import Services
from auoreo.schema.ops import (
    AuditEntry,
    AuditPage,
    AuditVerify,
    DoctorCheck,
    DoctorFix,
    DoctorFixFailure,
    DoctorFixPlan,
    DoctorFixResult,
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


@router.get("/doctor/fixes", response_model=DoctorFixPlan)
async def doctor_fixes(svc: ServicesDep) -> DoctorFixPlan:
    """The safe fixes `POST` would apply now: missing runtime dirs, loose file modes, stale worktrees."""
    fixes = await doctor_fix.plan(svc.settings, svc.cwd)
    return DoctorFixPlan(fixes=[DoctorFix(**f.as_dict()) for f in fixes])


@router.post("/doctor/fixes", response_model=DoctorFixResult)
async def apply_doctor_fixes(svc: ServicesDep) -> DoctorFixResult:
    """Apply every safe fix (re-planned here, never taken from the client); each one is audited."""
    applied: list[DoctorFix] = []
    failed: list[DoctorFixFailure] = []
    for f in await doctor_fix.plan(svc.settings, svc.cwd):
        try:
            await asyncio.to_thread(f.apply)  # worktree fixes run their own event loop
        except Exception as exc:
            failed.append(DoctorFixFailure(**f.as_dict(), error=redact(str(exc))[:300]))
            continue
        applied.append(DoctorFix(**f.as_dict()))
        await svc.audit.record(actor="user", action=f"doctor.fix.{f.kind}", detail={"target": f.target})
    return DoctorFixResult(applied=applied, failed=failed)


def _entry(row: AuditRow, svc: Services) -> AuditEntry:
    return AuditEntry(
        seq=row.seq,
        ts=row.ts,
        session_id=row.session_id,
        actor=row.actor,
        action=row.action,
        decision=row.decision,
        reason=redact(row.reason),
        detail=svc.audit.plain_detail(row),  # W1-E: decrypt, then redact
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
    return AuditPage(rows=[_entry(r, svc) for r in rows], next_after_seq=nxt)


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
