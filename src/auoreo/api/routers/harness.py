from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, status
from pydantic import ValidationError

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core import mcp_bundled
from auoreo.core.env_store import EnvStoreError
from auoreo.core.reload_kinds import ReloadKind, scoped_reload
from auoreo.harness.loader import LoadError
from auoreo.harness.registry import HarnessError
from auoreo.schema.chat import BundledMcpResult, HarnessEntry, HarnessListing, ReloadResult

if TYPE_CHECKING:
    from auoreo.core.services import Services

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


async def _reload(svc: Services, kind: ReloadKind | None) -> ReloadResult:
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
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"settings: {exc}") from exc
    scope = {"kind": kind} if kind else {}
    await svc.emit("server", "harness.reloaded", {"generation": gen, "errors": errors, **scope, **changes})
    return ReloadResult(generation=gen, errors=errors, changes=changes, kind=kind, note=note)


@router.post("/reload", response_model=ReloadResult)
async def reload(svc: ServicesDep, kind: ReloadKind | None = None) -> ReloadResult:
    """Re-discover all definition roots. Takes effect at the next turn. `?kind=` (I5) scopes
    the side effects and the reported diff: agent|command|mcp|lsp|config|model."""
    return await _reload(svc, kind)


@router.post("/mcp/bundled/{bundle}", response_model=BundledMcpResult)
async def enable_bundled_mcp(bundle: str, svc: ServicesDep) -> BundledMcpResult:
    """Turn a bundled MCP server on: copy `mcp/<bundle>.yaml` into `<home>/mcp/` with
    `enabled: true`, then reload the `mcp` kind (the API twin of `auoreo mcp add --bundle`)."""
    try:
        doc = mcp_bundled.bundled_doc(bundle)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc.args[0])) from exc
    try:
        path = mcp_bundled.write_definition(svc.settings.home, doc)
    except FileExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{exc} exists; edit it in place") from exc
    except LoadError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await svc.audit.record(
        actor="api",
        action="mcp.bundled.enabled",
        decision="written",
        detail={"name": str(doc["name"]), "path": str(path)},
    )
    result = await _reload(svc, "mcp")
    return BundledMcpResult(
        name=str(doc["name"]), path=str(path), generation=result.generation, errors=result.errors
    )
