"""Workspace trust (I4): `GET/POST /api/workspace/trust` for the server's `<cwd>/.auoreo/`.

Trusting reloads the harness so the project's hooks, MCP servers, tools, plugins, policies
and permission allow-rules take effect at the next turn; revoking reloads without them.
"""

from __future__ import annotations

import os

from fastapi import APIRouter

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.services import Services
from auoreo.harness import trust as trust_mod
from auoreo.schema.i4 import WorkspaceTrust, WorkspaceTrustIn

router = APIRouter(prefix="/api/workspace", tags=["workspace"], dependencies=AUTH)


def _status(svc: Services) -> WorkspaceTrust:
    trusted = trust_mod.is_trusted(svc.settings.home, svc.cwd)
    skipped = [] if trusted else [f"{k}/{n}" for k, n in trust_mod.skipped_in(svc.cwd)]
    return WorkspaceTrust(
        workspace=os.path.realpath(svc.cwd), trusted=trusted, trust_all=trust_mod.trust_all(), skipped=skipped
    )


@router.get("/trust", response_model=WorkspaceTrust)
async def get_trust(svc: ServicesDep) -> WorkspaceTrust:
    return _status(svc)


@router.post("/trust", response_model=WorkspaceTrust)
async def set_trust(svc: ServicesDep, body: WorkspaceTrustIn | None = None) -> WorkspaceTrust:
    revoke = bool(body and body.revoke)
    before = _status(svc)
    if revoke:
        trust_mod.untrust(svc.settings.home, svc.cwd)
    else:
        trust_mod.trust(svc.settings.home, svc.cwd, actor="api")
    await svc.audit.record(
        actor="user",
        action="workspace.untrust" if revoke else "workspace.trust",
        detail={"workspace": before.workspace, "unlocked": before.skipped},
    )
    diff = svc.harness.reload()
    if svc.mcp is not None:
        await svc.mcp.sync()
    gen, errors = diff.pop("generation"), diff.pop("errors")
    await svc.emit("server", "harness.reloaded", {"generation": gen, "errors": errors, **diff})
    status = _status(svc)
    await svc.emit("server", "workspace.trust_changed", status.model_dump())
    return status
