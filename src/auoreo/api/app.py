"""FastAPI app factory: one app, one port. Routers first, optional SPA mount last."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from auoreo import __version__
from auoreo.api.routers import chat, events, harness, health, memory, permissions, questions, sessions
from auoreo.core.services import Services
from auoreo.core.settings import Settings, get_settings
from auoreo.gateway.types import Gateway

log = logging.getLogger(__name__)
ROUTERS = (health, sessions, chat, events, harness, permissions, questions, memory)


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
            yield
        finally:
            if owned:
                await svc.close()

    app = FastAPI(title="auoreo", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for module in ROUTERS:
        app.include_router(module.router)
    dist = web_dist or Path(__file__).resolve().parents[3] / "web" / "dist"
    if (dist / "index.html").is_file():  # mounted AFTER routers so /api/* wins
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")
    return app
