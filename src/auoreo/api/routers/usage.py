"""Usage ledger (spec §20.2): per-call rows and a per-model / per-session summary."""

from typing import Literal

from fastapi import APIRouter

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.schema.chat import UsageRecord, UsageSummary
from auoreo.schema.insights import SessionSpend, UsageBudget, UsagePoint, UsageSeries

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
            cache_write_tokens=r.cache_write_tokens or 0,  # --- I2
            reasoning_tokens=r.reasoning_tokens or 0,
            generation_id=r.generation_id,
            deployment=r.deployment,
        )
        for r in rows
    ]


@router.get("/summary", response_model=UsageSummary)
async def summary(svc: ServicesDep, days: int = 7) -> UsageSummary:
    return UsageSummary.model_validate(await svc.coordinator.runner.usage.summary(max(0, min(days, 3650))))


@router.get("/timeseries", response_model=UsageSeries)
async def timeseries(
    svc: ServicesDep, days: int = 7, bucket: Literal["hour", "day"] | None = None
) -> UsageSeries:
    """Token/cost chart data plus the budget guards (`max_cost_usd`, per-turn loop budgets)."""
    days = max(1, min(days, 3650))
    size = bucket or ("hour" if days <= 1 else "day")
    ledger = svc.coordinator.runner.usage
    points = await ledger.series(days, size)
    st = svc.settings
    cap = st.max_cost_usd
    top = (await ledger.summary(days))["by_session"][:10]
    sessions = [
        SessionSpend(
            session_id=r["key"],
            cost_usd=r["cost_usd"],
            ratio=(r["cost_usd"] / cap) if cap else None,
            blocked=bool(cap) and r["cost_usd"] >= (cap or 0),
        )
        for r in top
    ]
    return UsageSeries(
        days=days,
        bucket=size,
        points=[UsagePoint.model_validate(p) for p in points],
        budget=UsageBudget(
            max_cost_usd=cap, token_budget=st.loop.token_budget, run_budget_seconds=st.loop.run_budget_seconds
        ),
        sessions=sessions,
    )
