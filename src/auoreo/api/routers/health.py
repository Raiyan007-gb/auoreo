from fastapi import APIRouter

from auoreo import __version__
from auoreo.api.deps import ServicesDep
from auoreo.schema.chat import Health

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=Health)
async def health(svc: ServicesDep) -> Health:
    return Health(
        status="ok",
        version=__version__,
        generation=svc.harness.generation,
        gateway=svc.gateway.name,
    )
