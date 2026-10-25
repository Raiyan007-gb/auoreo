"""FastAPI app factory: one app, one port. Routers first, optional SPA mount last."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.routing import Route
from starlette.types import ASGIApp, Scope

from auoreo import __version__
from auoreo.api import mcp_server, ws
from auoreo.api.routers import acp as acp_router
from auoreo.api.routers import (
    chat,
    compaction,
    events,
    harness,
    health,
    memory,
    permissions,
    questions,
    sessions,
    usage,
)
from auoreo.api.routers import commands as commands_router
from auoreo.api.routers import cron as cron_router
from auoreo.api.routers import deliveries as deliveries_router
from auoreo.api.routers import hooks as hooks_router
from auoreo.api.routers import kanban as kanban_router
from auoreo.api.routers import openai_compat as openai_compat_router
from auoreo.api.routers import plugins as plugins_router
from auoreo.api.routers import rtc as rtc_router
from auoreo.api.routers import teams as teams_router
from auoreo.api.routers import voice as voice_router
from auoreo.api.routers import webhooks as webhooks_router
from auoreo.core.services import Services
from auoreo.core.settings import Settings, get_settings
from auoreo.gateway.types import Gateway

log = logging.getLogger(__name__)
ROUTERS = (
    health,
    sessions,
    chat,
    events,
    harness,
    permissions,
    questions,
    memory,
    compaction,
    usage,
    commands_router,
    ws,
    rtc_router,
    hooks_router,
    cron_router,
    webhooks_router,
    deliveries_router,
    teams_router,
    kanban_router,
    plugins_router,  # M5e
    openai_compat_router,  # M5h
    acp_router,  # M5h
    voice_router,  # M4c
)
MOUNTS: list[tuple[str, Callable[[Settings], ASGIApp | None]]] = [
    ("/mcp", mcp_server.make_mount),  # M5h
]
"""Raw ASGI endpoints added after the routers and before the SPA: `(path, factory)`.
A factory returns None to skip; an app with `lifespan(services)` has it entered by the host."""


class SpaStaticFiles(StaticFiles):
    """Static files with an SPA fallback: unknown extension-less paths get index.html (200),
    so deep links like /session/<id> work; missing assets still 404."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            response = Response(status_code=404)
        if response.status_code == 404 and "." not in path.rsplit("/", 1)[-1]:
            return await super().get_response("index.html", scope)
        return response


def create_app(
    settings: Settings | None = None,
    *,
    services: Services | None = None,
    gateway: Gateway | None = None,
    cwd: Path | None = None,
    web_dist: Path | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = services is None
        svc = services or await Services.create(settings, cwd=cwd, gateway=gateway)
        app.state.services = svc
        if not settings.server_password:
            log.warning("server is unsecured: AUOREO_SERVER_PASSWORD is not set")
        try:
            async with AsyncExitStack() as stack:
                for mounted in app.state.mounts:
                    if (enter := getattr(mounted, "lifespan", None)) is not None:
                        await stack.enter_async_context(enter(svc))
                yield
        finally:
            await svc.connections.close_all(1001)
            if owned:
                await svc.close()

    app = FastAPI(title="auoreo", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for module in ROUTERS:
        app.include_router(module.router)
    app.state.mounts = []
    for path, factory in MOUNTS:
        if (asgi := factory(settings)) is not None:  # a Route (not Mount): exact path, no redirect
            app.router.routes.append(Route(path, endpoint=asgi))
            app.state.mounts.append(asgi)
    dist = web_dist or Path(__file__).resolve().parents[3] / "web" / "dist"
    if (dist / "index.html").is_file():  # mounted AFTER routers so /api/* wins
        app.mount("/", SpaStaticFiles(directory=dist, html=True), name="ui")
    return app
