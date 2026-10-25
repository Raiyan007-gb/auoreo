"""`GET /api/commands` — slash commands discovered from `commands/*.md`."""

from fastapi import APIRouter

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.schema.chat import CommandInfo

router = APIRouter(prefix="/api", tags=["commands"], dependencies=AUTH)


@router.get("/commands", response_model=list[CommandInfo])
async def list_commands(svc: ServicesDep) -> list[CommandInfo]:
    return [
        CommandInfo(
            name=c.name,
            description=c.manifest.description,
            handler=c.manifest.handler,
            args_hint=c.manifest.args_hint,
        )
        for c in sorted(svc.harness.commands.values(), key=lambda c: c.name)
    ]
