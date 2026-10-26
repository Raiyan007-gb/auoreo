"""FastAPI app factory: one app, one port. Routers first, optional SPA mount last."""

from __future__ import annotations

import logging
import mimetypes
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
from auoreo.api import mcp_server, security, ws
from auoreo.api.routers import acp as acp_router
from auoreo.api.routers import auth as auth_router  # --- G1a
from auoreo.api.routers import bridge as bridge_router  # --- W2-B
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
from auoreo.api.routers import context as context_router  # --- G5b
from auoreo.api.routers import cron as cron_router
from auoreo.api.routers import delegation as delegation_router  # --- H2
from auoreo.api.routers import deliveries as deliveries_router
from auoreo.api.routers import environments as environments_router  # --- W2-B

# --- W0-C
from auoreo.api.routers import goals as goals_router
from auoreo.api.routers import hooks as hooks_router
from auoreo.api.routers import kanban as kanban_router
from auoreo.api.routers import livekit as livekit_router  # --- I8
from auoreo.api.routers import meetings as meetings_router  # --- F4
from auoreo.api.routers import metrics as metrics_router  # --- G5c
from auoreo.api.routers import openai_compat as openai_compat_router
from auoreo.api.routers import ops as ops_router  # --- W0-B
from auoreo.api.routers import plugins as plugins_router
from auoreo.api.routers import policy as policy_router  # --- W1-C
from auoreo.api.routers import prefs as prefs_router  # --- G6
from auoreo.api.routers import rtc as rtc_router
from auoreo.api.routers import secrets_api as secrets_router  # --- I2
from auoreo.api.routers import session_extras as session_extras_router  # --- G7: plan + sources
from auoreo.api.routers import sessions_more as sessions_more_router  # --- G5a
from auoreo.api.routers import skills_hub as skills_hub_router  # --- G6
from auoreo.api.routers import suggestions as suggestions_router
from auoreo.api.routers import teams as teams_router
from auoreo.api.routers import voice as voice_router
from auoreo.api.routers import webhooks as webhooks_router
from auoreo.api.teardown import teardown  # --- H5
from auoreo.automation.webhooks import enforce_secrets  # --- G1a
from auoreo.core import logjson  # --- G5c
from auoreo.core import observability as obs
from auoreo.core.paths import web_dist_dir
from auoreo.core.services import Services
from auoreo.core.settings import Settings, get_settings
from auoreo.gateway.types import Gateway

log = logging.getLogger(__name__)
ROUTERS = (
    health,
    auth_router,  # --- G1a
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
    delegation_router,  # --- H2
    plugins_router,  # M5e
    openai_compat_router,  # M5h
    acp_router,  # M5h
    voice_router,  # M4c
    # --- W0-B
    ops_router,  # doctor + audit
    # --- W0-C
    goals_router,
    suggestions_router,
    # --- W1-C
    policy_router,
    # --- W2-B
    bridge_router,
    environments_router,
    # --- F4
    meetings_router,
    # --- G5b
    context_router,
    metrics_router,  # --- G5c: GET /metrics
    sessions_more_router,  # --- G5a: rename/share/retry/revert
    prefs_router,  # --- G6: PREFS.md proposals
    session_extras_router,  # --- G7: session plan + per-session MCP/plugin toggles
    skills_hub_router,  # --- G6: skill sources + hub approve
    secrets_router,  # --- I2: names-only list, write-through + rotate (hot reload)
    livekit_router,  # --- I8: POST /api/livekit/token + POST /livekit/webhook
)
MOUNTS: list[tuple[str, Callable[[Settings], ASGIApp | None]]] = [
    ("/mcp", mcp_server.make_mount),  # M5h
]
"""Raw ASGI endpoints added after the routers and before the SPA: `(path, factory)`.
A factory returns None to skip; an app with `lifespan(services)` has it entered by the host."""


mimetypes.add_type("application/manifest+json", ".webmanifest")  # not in every mime.types
NO_CACHE = {"sw.js", "sw-assets.json", "manifest.webmanifest", "index.html"}
"""PWA files the browser must revalidate so a new build's service worker is picked up."""


class SpaStaticFiles(StaticFiles):
    """Static files with an SPA fallback: unknown extension-less paths get index.html (200),
    so deep links like /session/<id> work; missing assets still 404."""

    password: str | None = None
    """Set by `create_app`: `index.html` gets the token meta only for a valid cookie."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            response = Response(status_code=404)
        if response.status_code == 404 and "." not in path.rsplit("/", 1)[-1]:
            path, response = "index.html", await super().get_response("index.html", scope)
        if path.lstrip("/") in NO_CACHE or path in ("", "."):
            response.headers["Cache-Control"] = "no-cache"
        if path.lstrip("/") in ("index.html", "", ".") and self.directory is not None:
            index = Path(self.directory) / "index.html"
            response = security.index_with_token(index, scope, self.password) or response
        if path.lstrip("/") == "sw.js":
            response.headers["Service-Worker-Allowed"] = "/"
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
    logjson.configure(settings)  # --- G5c: no-op unless logging.format = json

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = services is None
        svc = services or await Services.create(settings, cwd=cwd, gateway=gateway)
        app.state.services = svc
        enforce_secrets(svc)  # --- G1a: webhooks.strict_secrets refuses to start
        if not settings.server_password:
            log.warning("server is unsecured: AUOREO_SERVER_PASSWORD is not set")
        try:
            async with AsyncExitStack() as stack:
                for mounted in app.state.mounts:
                    if (enter := getattr(mounted, "lifespan", None)) is not None:
                        await stack.enter_async_context(enter(svc))
                yield
        finally:
            await teardown(app, svc, owned=owned)  # --- H5: spec §24.7 order

    app = FastAPI(title="auoreo", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    security.install(app, settings)  # --- G1a: Host check (outermost) + idempotency capture
    for module in ROUTERS:
        app.include_router(module.router)
    app.state.mounts = []
    for path, factory in MOUNTS:
        if (asgi := factory(settings)) is not None:  # a Route (not Mount): exact path, no redirect
            app.router.routes.append(Route(path, endpoint=asgi))
            app.state.mounts.append(asgi)
    obs.instrument_app(app, settings)  # --- W0-D: no-op unless AUOREO_OTEL_MODE=local|otlp
    dist = web_dist or web_dist_dir()
    if (dist / "index.html").is_file():  # mounted AFTER routers so /api/* wins
        spa = SpaStaticFiles(directory=dist, html=True)
        spa.password = settings.server_password
        app.mount("/", spa, name="ui")
    return app
