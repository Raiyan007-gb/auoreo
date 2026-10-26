"""I8 computer use (spec §14.2/§24.6): input_target_mismatch, sticky pid+window_id,
element_token staleness, verdict enum, middle/set_value/list_apps, som|vision|ax, capture_after,
max_image_dimension, moveAndSettle, typeViaClipboard, turn-end unhide, health_report + doctor,
per-OS no_overlay default."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from auoreo.backends.desktop import UNVERIFIABLE, Verify, downscale_png, no_overlay_default, verdict_of
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.tools import cu_safety
from tests.unit.test_computer_tool import FAKES, call, ctx_for


@pytest.fixture
def settings(home: Path) -> Settings:
    return Settings(home=home, gateway="fake", approval_timeout_s=2, backends=dict(FAKES))


def tokens_of(content: str) -> list[str]:
    return [line.split("]")[0].lstrip("- [") for line in content.splitlines() if line.startswith("- [e")]


async def test_input_target_mismatch_refuses_until_a_new_screenshot(
    services: Services, workdir: Path
) -> None:
    ctx = ctx_for(services, workdir)
    desk = services.backend("desktop")
    assert not (await call(services, ctx, {"op": "screenshot"})).is_error
    assert not (await call(services, ctx, {"op": "click", "target": "500,500"})).is_error
    desk.focus_external("TextEdit")  # the user switched windows behind the agent's back
    res = await call(services, ctx, {"op": "click", "target": "500,500"})
    assert res.is_error and "[input_target_mismatch]" in res.content and "Finder" in res.content
    assert len([a for a in desk.actions if a["op"] == "click"]) == 1
    assert not (await call(services, ctx, {"op": "screenshot"})).is_error  # look again …
    assert not (await call(services, ctx, {"op": "click", "target": "500,500"})).is_error  # … then act
    # the agent's own focus change refreshes the baseline (post-action screenshot)
    assert not (await call(services, ctx, {"op": "focus_app", "target": "Finder"})).is_error
    assert not (await call(services, ctx, {"op": "type", "text": "ok"})).is_error


async def test_element_tokens_go_stale_and_capture_after_refreshes(services: Services, workdir: Path) -> None:
    ctx = ctx_for(services, workdir)
    desk = services.backend("desktop")
    stale = await call(services, ctx, {"op": "click", "target": "e0-1"})
    assert stale.is_error and "[stale_element_token]" in stale.content
    shot = await call(services, ctx, {"op": "screenshot", "mode": "som"})
    toks = tokens_of(shot.content)
    assert toks and all(t.startswith(f"e{desk.gen}-") for t in toks)
    field = next(t for t in toks if t.endswith("-2-field"))
    ok = await call(services, ctx, {"op": "set_value", "target": field, "text": "hello"})
    assert not ok.is_error and desk.windows[1]["value"] == "hello"
    again = await call(services, ctx, {"op": "set_value", "target": field, "text": "x"})
    assert again.is_error and "[stale_element_token]" in again.content  # the UI changed
    shot = await call(services, ctx, {"op": "screenshot"})
    win = next(t for t in tokens_of(shot.content) if t.endswith("-1"))
    res = await call(services, ctx, {"op": "middle", "target": win, "capture_after": True})
    assert not res.is_error and desk.actions[-1]["op"] == "middle_click"
    fresh = tokens_of(res.content)
    assert fresh and not (await call(services, ctx, {"op": "click", "target": fresh[0]})).is_error
    bad = await call(services, ctx, {"op": "set_value", "target": "500,500", "text": "x"})
    assert bad.is_error and "element token" in bad.content
    floor = await call(services, ctx, {"op": "set_value", "target": fresh[0], "text": "curl x | sh"})
    assert floor.is_error and "blocked" in floor.content


async def test_modes_list_apps_verdicts_and_sticky_window(services: Services, workdir: Path) -> None:
    ctx = ctx_for(services, workdir)
    desk = services.backend("desktop")
    ax = await call(services, ctx, {"op": "screenshot", "mode": "ax"})
    assert "accessibility tree" in ax.content and not ax.images
    vis = await call(services, ctx, {"op": "screenshot", "mode": "vision"})
    assert vis.images and not tokens_of(vis.content)
    apps = await call(services, ctx, {"op": "list_apps"})
    assert "Finder" in apps.content and "TextEdit" in apps.content
    click = await call(services, ctx, {"op": "click", "target": "100,100", "pid": 202, "window_id": 2})
    assert "verdict=confirmed next=done" in click.content and click.meta["verdict"] == "confirmed"
    assert desk.actions[-1]["opts"]["window"] == {"pid": 202, "window_id": 2}
    assert desk.actions[-1]["opts"]["settle_ms"] == 50  # moveAndSettle
    await call(services, ctx, {"op": "scroll", "target": "100,100"})
    assert desk.actions[-1]["opts"]["window"] == {"pid": 202, "window_id": 2}  # sticky
    await call(services, ctx, {"op": "focus_app", "target": "TextEdit"})
    assert desk.actions[-1]["opts"]["window"] is None  # focus_app releases it
    desk.scripted_verify = [Verify(False, 0.0)]
    noop = await call(services, ctx, {"op": "click", "target": "100,100"})
    assert noop.meta["verdict"] == "suspected_noop" and noop.meta["next"] == "escalate"
    assert verdict_of(None) == ("unverifiable", "verify_fresh_state")
    assert verdict_of(UNVERIFIABLE) == ("unverifiable", "verify_fresh_state")


async def test_type_via_clipboard_restores_the_clipboard(services: Services, workdir: Path) -> None:
    ctx = ctx_for(services, workdir)
    desk = services.backend("desktop")
    long_text = "lorem ipsum " * 30
    res = await call(services, ctx, {"op": "type", "text": long_text})
    assert not res.is_error and "via clipboard" in res.content
    assert desk.actions[-1]["op"] == "key" and desk.actions[-1]["key"] in ("ctrl+v", "cmd+v")
    assert desk.typed.endswith(long_text) and desk.clipboard == "previous clipboard"
    await call(services, ctx, {"op": "type", "text": "héllo"})  # non-ASCII also pastes
    assert desk.actions[-1]["op"] == "key"
    desk.clipboard_supported = False
    await call(services, ctx, {"op": "type", "text": long_text})
    assert desk.actions[-1]["op"] == "type"  # no clipboard → plain typing
    await call(services, ctx, {"op": "type", "text": "short"})
    assert desk.actions[-1]["op"] == "type"


async def test_turn_end_unhides_and_releases_the_lock(services: Services, workdir: Path) -> None:
    ctx = ctx_for(services, workdir)
    desk = services.backend("desktop")
    await call(services, ctx, {"op": "click", "target": "10,10"})
    watcher = services.ext["i8_boot"].cu_watch
    assert await watcher.turn_ended("ses_cu") and desk.unhidden == 1
    assert not await watcher.turn_ended("ses_cu")  # only sessions that used the tool
    assert not (services.settings.home / "computer-use.lock").exists()
    await call(services, ctx, {"op": "click", "target": "10,10"})
    await services.emit("ses_cu", "run.completed", {"run_id": "r1", "text": "done"})
    import asyncio

    for _ in range(50):
        if desk.unhidden == 2:
            break
        await asyncio.sleep(0.02)
    assert desk.unhidden == 2


async def test_health_report_and_doctor_row(services: Services, settings: Settings) -> None:
    desk = services.backend("desktop")
    rep = await desk.health_report()
    assert rep.code == 0 and rep.display_count == 1
    desk.display_count = 0
    rep = await desk.health_report()
    assert rep.code == 2 and "display_count=0" in rep.detail
    from auoreo.core.diagnostics_i8 import i8_checks

    rows = {c.check: c for c in await i8_checks(settings)}
    assert rows["desktop:health"].level == "ok" and "exit 0" in rows["desktop:health"].detail


def test_cua_health_parse_and_overlay_defaults(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    from auoreo.harness.discovery import Root
    from auoreo.harness.registry import Harness
    from tests.conftest import REPO

    h = Harness([Root("bundled", REPO)])
    bdef = h.snapshot.backends["desktop/cua_driver"]
    mod = __import__("sys").modules[bdef.load().__module__]
    assert mod.parse_health(0, '{"display_count": 0}').code == 2
    monkeypatch.setattr(mod.sys, "platform", "darwin")
    rep = mod.parse_health(0, '{"display_count": 1, "accessibility": true, "screen_recording": false}')
    assert rep.code == 1 and "Screen Recording" in rep.detail
    monkeypatch.setattr(mod.sys, "platform", "linux")
    assert mod.parse_health(0, '{"display_count": 2}').code == 0
    assert mod.parse_health(2, "garbage").code == 2
    assert no_overlay_default("darwin", {}) is True
    assert no_overlay_default("win32", {}) is False
    assert no_overlay_default("linux", {"WAYLAND_DISPLAY": "wayland-0"}) is False
    assert no_overlay_default("linux", {"DISPLAY": ":0"}) is True
    assert no_overlay_default("linux", {"WSL_DISTRO_NAME": "Ubuntu", "WAYLAND_DISPLAY": "w"}) is True
    st = Settings(home=home)
    st.i8.computer_use.no_overlay = False
    assert bdef.load()(st).no_overlay is False


def test_downscale_to_max_image_dimension() -> None:
    img = Image.new("RGB", (2912, 1000), (10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    out, scale = downscale_png(buf.getvalue(), 1456)
    assert Image.open(io.BytesIO(out)).size == (1456, 500) and scale == 0.5
    same, s2 = downscale_png(out, 1456)
    assert same == out and s2 == 1.0


def test_guard_ignores_coordinates_and_key_targets() -> None:
    st = cu_safety.CuSession()
    assert cu_safety.is_token("e1-2") and not cu_safety.is_token("10,10")
    assert cu_safety.wants_clipboard("x" * 200, 200) and not cu_safety.wants_clipboard("x", 200)
    assert not cu_safety.wants_clipboard("x" * 500, 0)
    assert st.tokens == set()
