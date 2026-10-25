"""Usage ledger (spec §20.2): per-call rows and a per-model / per-session summary."""

from fastapi import APIRouter

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.schema.chat import UsageRecord, UsageSummary

router = APIRouter(prefix="/api/usage", tags=["usage"], dependencies=AUTH)


@router.get("", response_model=list[UsageRecord])
async def list_usage(
    svc: ServicesDep, session_id: str | None = None, limit: int = 200, children: bool = False
) -> list[UsageRecord]:
    """`children=true` (with `session_id`) rolls child sessions' usage into the parent."""
    rows = await svc.coordinator.runner.usage.by_session(
        session_id, limit=max(1, min(limit, 5000)), children=children
    )
    return [
        UsageRecord(
            id=r.id,
            session_id=r.session_id,
            run_id=r.run_id,
            model=r.model,
            prompt_tokens=r.prompt_tokens,
            completion_tokens=r.completion_tokens,
            cached_tokens=r.cached_tokens,
            cost_usd=r.cost_usd,
            estimated=r.estimated,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/summary", response_model=UsageSummary)
async def summary(svc: ServicesDep, days: int = 7) -> UsageSummary:
    return UsageSummary.model_validate(await svc.coordinator.runner.usage.summary(max(0, min(days, 3650))))
