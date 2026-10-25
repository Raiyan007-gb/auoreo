"""PWA static files: manifest content type, service-worker headers, API never shadowed."""

from __future__ import annotations

from pathlib import Path

import httpx
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core.services import Services
from auoreo.core.settings import Settings


async def test_pwa_files_served_with_pwa_headers(
    services: Services, settings: Settings, tmp_path: Path
) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>")
    (dist / "manifest.webmanifest").write_text('{"name": "AUOREO"}')
    (dist / "sw.js").write_text("self.addEventListener('fetch', () => {});")
    (dist / "sw-assets.json").write_text('{"build": "x", "assets": []}')
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)")
    app = create_app(settings, services=services, web_dist=dist)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        manifest = await c.get("/manifest.webmanifest")
        assert manifest.status_code == 200
        assert manifest.headers["content-type"].startswith("application/manifest+json")
        assert manifest.headers["cache-control"] == "no-cache"

        sw = await c.get("/sw.js")
        assert sw.status_code == 200
        assert "javascript" in sw.headers["content-type"]
        assert sw.headers["service-worker-allowed"] == "/"
        assert sw.headers["cache-control"] == "no-cache"

        assert (await c.get("/sw-assets.json")).headers["cache-control"] == "no-cache"
        deep = await c.get("/sessions")  # SPA fallback is revalidated too
        assert deep.status_code == 200 and deep.headers["cache-control"] == "no-cache"
        asset = await c.get("/assets/index-abc123.js")  # hashed assets keep default caching
        assert asset.status_code == 200 and "service-worker-allowed" not in asset.headers
        assert "cache-control" not in asset.headers

        health = await c.get("/api/health")  # routers still win over the static mount
        assert health.headers["content-type"].startswith("application/json")
        assert "service-worker-allowed" not in health.headers
