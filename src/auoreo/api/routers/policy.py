"""Model/effort bandit (W1-C, spec §12.5): status and reset."""

from fastapi import APIRouter

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.policy.bandit import policy_of
from auoreo.schema.policy import BanditResetRequest, BanditResetResult, BanditStatus

router = APIRouter(prefix="/api/policy", tags=["policy"], dependencies=AUTH)


@router.get("/bandit", response_model=BanditStatus)
async def bandit_status(svc: ServicesDep) -> BanditStatus:
    """Settings, configured arms, per-context arm stats and the 20 latest outcomes."""
    return BanditStatus.model_validate(await policy_of(svc).status())


@router.post("/bandit/reset", response_model=BanditResetResult)
async def bandit_reset(svc: ServicesDep, body: BanditResetRequest | None = None) -> BanditResetResult:
    """Forget learned stats and outcomes (one `context`, or all). Settings are unchanged."""
    arms, outcomes = await policy_of(svc).store.reset(body.context if body else None)
    await svc.audit.record(
        actor="user", action="policy.bandit.reset", detail={"context": body.context if body else None}
    )
    return BanditResetResult(arms=arms, outcomes=outcomes)
