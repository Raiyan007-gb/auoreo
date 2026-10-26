"""I7 `/api/oauth/{connector}/settled`: `auoreo login` (another process) tells the running
server that `oauth/<connector>` changed. The server drops its cached access token and emits
`authorization/settled` (spec §25.14) so a second tab / waiting client can continue. The
refresh token itself never crosses the API."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, status

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core import oauth_login as oauth
from auoreo.schema.oauth import OAuthSettle, OAuthSettled

router = APIRouter(prefix="/api/oauth", tags=["oauth"], dependencies=AUTH)
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@router.post("/{connector}/settled", response_model=OAuthSettled)
async def settled(connector: str, body: OAuthSettle, svc: ServicesDep) -> OAuthSettled:
    """Drop the cached access token for `connector` and emit `authorization/settled`."""
    if not NAME_RE.match(connector):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid connector name")
    if not svc.secrets.get(oauth.secret_name(connector), by="oauth"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"not signed in to {connector}")
    oauth.tokens_for(svc).forget(connector)
    await svc.emit("server", oauth.SETTLED_EVENT, {"connector": connector, "flow": body.flow})
    return OAuthSettled(connector=connector, settled=True, flow=body.flow)
