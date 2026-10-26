from fastapi import APIRouter, HTTPException, status
from pydantic import ValidationError

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.env_store import EnvStoreError
from auoreo.core.reload_kinds import ReloadKind, scoped_reload
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
async def reload(svc: ServicesDep, kind: ReloadKind | None = None) -> ReloadResult:
    """Re-discover all definition roots. Takes effect at the next turn. `?kind=` (I5) scopes
    the side effects and the reported diff: agent|command|mcp|lsp|config|model."""
    try:
        diff = svc.harness.reload()
    except HarnessError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, [str(e) for e in exc.errors]) from exc
    gen, errors = diff.pop("generation"), diff.pop("errors")
    note: str | None = None
    if kind is None:
        if svc.mcp is not None:
            await svc.mcp.sync()
        changes = diff
    else:
        try:
            changes, note = await scoped_reload(svc, kind, diff)
        except (ValidationError, EnvStoreError) as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"settings.json: {exc}") from exc
    scope = {"kind": kind} if kind else {}
    await svc.emit("server", "harness.reloaded", {"generation": gen, "errors": errors, **scope, **changes})
    return ReloadResult(generation=gen, errors=errors, changes=changes, kind=kind, note=note)
