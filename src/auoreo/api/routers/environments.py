"""`GET /api/environments` — where commands and mail can go, with live check() status."""

from fastapi import APIRouter

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.remote.environments import list_environments
from auoreo.schema.bridge import EnvironmentInfo

router = APIRouter(prefix="/api", tags=["environments"], dependencies=AUTH)


@router.get("/environments", response_model=list[EnvironmentInfo])
async def environments(svc: ServicesDep) -> list[EnvironmentInfo]:
    """Environment backends, named environments, the exec-server and bridge peers. No secrets."""
    return await list_environments(svc)
