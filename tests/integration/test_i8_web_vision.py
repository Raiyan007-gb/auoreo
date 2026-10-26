"""I8 web computer use (Playwright trace, Chrome CDP bridge: approval gate, loopback-only,
honest check, real connect_over_cdp to a locally launched Chromium) and vision details
(scale_note remap, 64 px floor, 5 MB / 8000 px retry, native temperature 0.1, aux fallback)."""

from __future__ import annotations

import base64
import io
import json
import os
import socket
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.core.settings import ModelGroup, Settings
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.fake import FakeGateway
from auoreo.gateway.types import ImagePart, ToolCall
from auoreo.harness.manifests import Rule
from auoreo.tools import images
from auoreo.tools.context import ToolContext, ToolResult
from tests.integration.test_browser_playwright import (
    _Handler,  # noqa: F401  (fixture site handler)
    site,  # noqa: F401
)
from tests.media import png


@pytest.fixture(autouse=True)
def _browsers_path(monkeypatch: pytest.MonkeyPatch) -> None:
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH") and Path("/opt/pw-browsers").is_dir():
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")


def ctx_for(svc: Services, workdir: Path, rules: list[Rule], sid: str = "ses_web") -> ToolContext:
    return ToolContext(
        session_id=sid,
        cwd=workdir,
        home=svc.settings.home,
        harness=svc.harness,
        interactive=False,
        services={"services": svc, "engine": PermissionEngine(rules)},
    )


async def run_tool(svc: Services, ctx: ToolContext, name: str, args: dict[str, Any]) -> ToolResult:
    out = await svc.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)),
        tools=svc.harness.tools,
        ctx=ctx,
        engine=ctx.services["engine"],
    )
    return out.result


ALLOW_WEB = [
    Rule(permission="webfetch", pattern="*", action="allow"),
    Rule(permission="screen", pattern="*", action="allow"),
]


# ------------------------------------------------------------------ browser
async def test_playwright_trace_saved_under_the_session_dir(home: Path, workdir: Path, site: str) -> None:  # noqa: F811
    pytest.importorskip("playwright")
    st = Settings(home=home, gateway="fake", backends={"browser": "playwright"}, allow_private_urls=True)
    st.i8.browser.trace = True
    svc = await Services.create(st, cwd=workdir, gateway=FakeGateway(), with_mcp=False, with_memory=False)
    try:
        browser = svc.backend("browser")
        if not (await browser.check()).ok:
            pytest.skip("Chromium cannot launch here")
        await browser.navigate("ses_trace", site + "/")
        await browser.close_session("ses_trace")
        (trace,) = browser.traces
        assert trace.parent == home / "sessions" / "ses_trace" and trace.name.startswith("browser-trace-")
        assert trace.stat().st_size > 1000 and trace.read_bytes()[:2] == b"PK"  # a zip
    finally:
        await svc.close()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
async def cdp_chrome() -> AsyncIterator[str]:
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    port = free_port()
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(args=[f"--remote-debugging-port={port}"])
        except Exception as exc:
            pytest.skip(f"Chromium cannot launch: {str(exc).splitlines()[0]}")
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            await browser.close()


async def test_chrome_cdp_bridge_is_gated_and_real(
    home: Path,
    workdir: Path,
    site: str,  # noqa: F811
    cdp_chrome: str,
) -> None:
    st = Settings(home=home, gateway="fake", backends={"browser": "chrome_cdp"}, allow_private_urls=True)
    st.i8.browser.cdp_url = cdp_chrome
    svc = await Services.create(st, cwd=workdir, gateway=FakeGateway(), with_mcp=False, with_memory=False)
    try:
        browser = svc.backend("browser")
        status = await browser.check()
        assert status.ok and "via CDP" in status.detail, status
        # no browser_cdp rule + non-interactive → the real-profile bridge refuses
        res = await run_tool(svc, ctx_for(svc, workdir, ALLOW_WEB), "browser_navigate", {"url": site + "/"})
        assert res.is_error and "browser_cdp" in res.content and "approval" in res.content
        rules = [*ALLOW_WEB, Rule(permission="browser_cdp", pattern="*", action="allow")]
        res = await run_tool(svc, ctx_for(svc, workdir, rules), "browser_navigate", {"url": site + "/"})
        assert not res.is_error and "Fixture site" in res.content, res.content
        res = await run_tool(svc, ctx_for(svc, workdir, rules), "browser_close", {})
        assert not res.is_error
        assert (await browser.check()).ok  # the user's Chrome is still running
    finally:
        await svc.close()


async def test_chrome_cdp_honest_check_and_loopback_only(home: Path) -> None:
    from auoreo.harness.discovery import Root
    from auoreo.harness.registry import Harness
    from tests.conftest import REPO

    st = Settings(home=home)
    backend = Harness([Root("bundled", REPO)]).snapshot.backends["browser/chrome_cdp"].load()(st)
    st.i8.browser.cdp_url = f"http://127.0.0.1:{free_port()}"
    status = await backend.check()
    assert (
        not status.ok
        and "no Chrome DevTools endpoint" in status.detail
        and "remote-debugging-port" in status.remedy
    )
    st.i8.browser.cdp_url = "http://10.0.0.5:9222"
    status = await backend.check()
    assert not status.ok and "not loopback" in status.detail
    assert backend.approval_permission == "browser_cdp"


# ------------------------------------------------------------------- vision
@pytest.fixture
def settings(home: Path) -> Settings:
    return Settings(home=home, gateway="fake", backends={"capture": "fake"})


async def test_region_screenshot_carries_scale_note(services: Services, workdir: Path) -> None:
    ctx = ctx_for(services, workdir, ALLOW_WEB)
    res = await run_tool(services, ctx, "screenshot", {"region": [100, 50, 200, 100]})
    assert not res.is_error, res.content
    assert "scale_note: image pixel (x, y) is screen pixel (100 + x*1, 50 + y*1)" in res.content
    assert res.meta["offset_x"] == 100 and res.meta["offset_y"] == 50 and res.meta["scale_x"] == 1.0
    from tools.screenshot.handler import scale_note  # type: ignore[import-not-found]

    note, meta = scale_note((10, 20, 3136, 2000), (3136, 2000), (1568, 1000))
    assert meta["scale_x"] == 2.0 and "10 + x*2" in note


def test_downscale_floor_keeps_64px_short_edge() -> None:
    wide = Image.new("RGB", (8000, 100), (200, 10, 10))
    buf = io.BytesIO()
    wide.save(buf, "PNG")
    out, _, meta = images.normalize(buf.getvalue())
    assert min(meta["width"], meta["height"]) >= 64
    big = Image.effect_noise((9000, 1200), 80).convert("RGB")
    raw = io.BytesIO()
    big.save(raw, "JPEG", quality=85)
    fitted, media = images.fit_for_provider(raw.getvalue(), max_bytes=200_000)
    img = Image.open(io.BytesIO(fitted))
    assert media == "image/jpeg" and max(img.size) <= 8000 and len(fitted) <= 200_000


async def test_vision_native_temperature_size_retry_and_aux_fallback(
    services: Services, gateway: FakeGateway, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (workdir / "a.png").write_bytes(png())
    ctx = ctx_for(services, workdir, [Rule(permission="read", pattern="*", action="allow")])
    real = gateway.stream
    seen: list[Any] = []
    fails = {"n": 1, "err": GatewayError("INVALID_REQUEST", "image exceeds 5 MB maximum", status=400)}

    def stream(req: Any) -> Any:
        seen.append(req)
        if fails["n"] > 0:
            fails["n"] -= 1
            raise fails["err"]
        return real(req)

    monkeypatch.setattr(gateway, "stream", stream)
    res = await run_tool(services, ctx, "vision_understand", {"path": "a.png", "question": "what?"})
    assert not res.is_error, res.content
    assert res.meta["path"] == "native" and len(seen) == 2  # one downscale retry
    assert all(r.temperature == 0.1 for r in seen)
    url = seen[1].messages[-1]["content"][1]["image_url"]["url"]
    assert "(downscaled)" in seen[1].messages[-1]["content"][0]["text"] or url.startswith("data:image/jpeg")
    # a native failure that is not about size falls back to the `vision` group (fail closed to aux)
    services.settings.models["vision"] = ModelGroup(models=["openai/gpt-4o"])
    seen.clear()
    fails.update(n=1, err=GatewayError("SERVER", "boom", status=500))
    res = await run_tool(services, ctx, "vision_understand", {"path": "a.png", "question": "what?"})
    assert not res.is_error and res.meta["path"] == "aux" and [r.model for r in seen] == ["default", "vision"]


def test_halve_reduces_an_image_part() -> None:
    img = Image.new("RGB", (1000, 800), (1, 2, 3))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    part = ImagePart("image/png", base64.b64encode(buf.getvalue()).decode(), "x")
    small = images._halve(part)
    assert Image.open(io.BytesIO(base64.b64decode(small.data_b64))).size == (500, 400)
