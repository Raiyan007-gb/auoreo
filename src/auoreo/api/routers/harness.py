from fastapi import APIRouter, HTTPException, status

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.harness.registry import HarnessError
from auoreo.schema.chat import HarnessEntry, HarnessListing, ReloadResult

router = APIRouter(prefix="/api/harness", tags=["harness"], dependencies=AUTH)


@router.get("", response_model=HarnessListing)
async def listing(svc: ServicesDep) -> HarnessListing:
    entries = {k: [HarnessEntry(**r) for r in rows] for k, rows in svc.harness.describe().items()}
    return HarnessListing(
        generation=svc.harness.generation,
        entries=entries,
        errors=[str(e) for e in svc.harness.errors],
    )


@router.get("/{kind}", response_model=list[HarnessEntry])
async def by_kind(kind: str, svc: ServicesDep) -> list[HarnessEntry]:
    rows = svc.harness.describe().get(kind)
    if rows is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown kind '{kind}'")
    return [HarnessEntry(**r) for r in rows]


@router.post("/reload", response_model=ReloadResult)
async def reload(svc: ServicesDep) -> ReloadResult:
    """Re-discover all definition roots. Takes effect at the next turn."""
    try:
        diff = svc.harness.reload()
    except HarnessError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, [str(e) for e in exc.errors]) from exc
    if svc.mcp is not None:
        await svc.mcp.sync()
    gen, errors = diff.pop("generation"), diff.pop("errors")
    await svc.emit("server", "harness.reloaded", {"generation": gen, "errors": errors, **diff})
    return ReloadResult(generation=gen, errors=errors, changes=diff)
