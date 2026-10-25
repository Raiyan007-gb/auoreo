"""M4d desktop backends: honest `cua_driver.check()`, its MCP call mapping (contract only),
the generic verify/coordinate helpers and the doctor rows for every voice/desktop kind."""

from __future__ import annotations

import io
import stat
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from auoreo.backends.desktop import compare, to_pixels
from auoreo.core.settings import Settings
from auoreo.harness.discovery import Root
from auoreo.harness.registry import Harness
from auoreo.tools.context import ToolError
from tests.conftest import REPO


def cua(settings: Settings) -> Any:
    h = Harness([Root("bundled", REPO)])
    return h.snapshot.backends["desktop/cua_driver"].load()(settings)


def fake_driver(tmp_path: Path, version: str) -> str:
    exe = tmp_path / "bin" / "cua-driver"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text(f"#!/bin/sh\necho 'cua-driver {version}'\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return str(exe)


def png(colour: tuple[int, int, int], size: tuple[int, int] = (200, 100), box: bool = False) -> bytes:
    img = Image.new("RGB", size, colour)
    if box:
        img.paste((255, 255, 255), (20, 20, 80, 80))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


async def test_cua_driver_check_is_honest_without_binary(home: Path) -> None:
    st = Settings(home=home, cua_driver_command="definitely-not-cua-driver")
    status = await cua(st).check()
    assert status.ok is False and "not installed" in status.detail and "0.20" in status.remedy
    with pytest.raises(ToolError, match="not ready"):
        await cua(st).act("click", x=1, y=1)


async def test_cua_driver_check_display_and_version_floor(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    old = Settings(home=home, cua_driver_command=fake_driver(tmp_path / "old", "0.19.4"))
    status = await cua(old).check()
    if status.detail.startswith("no display"):  # Linux without a display: that is checked first
        monkeypatch.setenv("DISPLAY", ":99")
        status = await cua(old).check()
    assert status.ok is False and "older than 0.20" in status.detail
    new = Settings(home=home, cua_driver_command=fake_driver(tmp_path / "new", "0.21.0"))
    assert (await cua(new).check()).ok is True
    monkeypatch.delenv("DISPLAY", raising=False)
    import sys

    if sys.platform.startswith("linux"):
        status = await cua(new).check()
        assert status.ok is False and "no display" in status.detail


def test_cua_child_env_is_scrubbed_and_telemetry_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak")
    h = Harness([Root("bundled", REPO)])
    mod = __import__(h.snapshot.backends["desktop/cua_driver"].load().__module__, fromlist=["child_env"])
    env = mod.child_env(False)
    assert "OPENAI_API_KEY" not in env and env["CUA_DRIVER_RS_TELEMETRY_ENABLED"] == "0"
    assert "CUA_DRIVER_RS_TELEMETRY_ENABLED" not in mod.child_env(True)
    assert mod.parse_version("cua-driver 0.20.1 (abc)") == (0, 20, 1)


async def test_cua_driver_maps_ops_to_mcp_tools(home: Path) -> None:
    backend = cua(Settings(home=home))
    calls: list[tuple[str, dict[str, Any]]] = []
    shots = iter([png((0, 0, 0), (1000, 500)), png((0, 0, 0), (1000, 500), box=True)] * 10)

    class Block:
        def __init__(self, data: bytes) -> None:
            import base64

            self.data, self.mime_type = base64.b64encode(data).decode(), "image/png"

    class Result:
        def __init__(self, tool: str) -> None:
            self.is_error = False
            self.content = [Block(next(shots))] if tool == "get_desktop_state" else []

    async def fake_call(tool: str, args: dict[str, Any]) -> Result:
        calls.append((tool, args))
        return Result(tool)

    backend._call = fake_call
    res = await backend.act("click", x=500, y=1000)
    assert res.ok and res.verify is not None and res.verify.matched
    assert [c[0] for c in calls] == ["get_desktop_state", "click", "get_desktop_state"]
    click = calls[1][1]
    assert click["x"] == 500 and click["y"] == 499 and click["delivery_mode"] == "background"
    assert click["target"] == {"kind": "desktop", "display_id": "primary"}

    calls.clear()
    await backend.act("key", key="cmd+shift+t", delivery_mode="foreground")
    assert calls[1] == (
        "hotkey",
        {
            "delivery_mode": "foreground",
            "target": {"kind": "desktop", "display_id": "primary"},
            "keys": ["cmd", "shift", "t"],
        },
    )
    calls.clear()
    await backend.act("key", key="enter")
    assert calls[1][0] == "press_key" and calls[1][1]["key"] == "enter"
    calls.clear()
    await backend.act("type", target="s1:14", text="hello")
    assert (
        calls[1][0] == "type_text" and calls[1][1]["element_token"] == "s1:14" and "target" not in calls[1][1]
    )
    calls.clear()
    await backend.act("scroll", direction="up", amount=2)
    assert calls[1] == (
        "scroll",
        {
            "delivery_mode": "background",
            "target": {"kind": "desktop", "display_id": "primary"},
            "direction": "up",
            "amount": 2,
        },
    )
    calls.clear()
    await backend.act("focus_app", target="Safari")
    assert calls[1] == ("launch_app", {"name": "Safari"})
    calls.clear()
    shot = await backend.act("screenshot")
    assert shot.screenshot_png and [c[0] for c in calls] == ["get_desktop_state"]


def test_compare_and_to_pixels() -> None:
    same = compare(png((10, 10, 10)), png((10, 10, 10)))
    assert same.matched is False and same.score == 0.0
    changed = compare(png((10, 10, 10)), png((10, 10, 10), box=True))
    assert changed.matched is True and changed.score > 0.1
    assert compare(None, png((0, 0, 0))).matched is False
    assert to_pixels(0, 0, 1920, 1080) == (0, 0)
    assert to_pixels(1000, 1000, 1920, 1080) == (1919, 1079)
    assert to_pixels(500, 500, 1001, 1001) == (500, 500)


def test_doctor_lists_voice_and_desktop_kinds(home: Path) -> None:
    from auoreo.cli.commands.doctor import _backend_checks

    h = Harness([Root("bundled", REPO)])
    rows = {r["check"]: r for r in _backend_checks(Settings(home=home), h)}
    for kind in ("stt", "tts", "vad", "wake", "desktop"):
        assert f"backend:{kind}" in rows
    assert rows["backend:vad"]["level"] == "ok" and rows["backend:wake"]["level"] == "ok"
    assert rows["backend:stt"]["level"] == "warn" and "faster_whisper" in rows["backend:stt"]["detail"]
    assert rows["backend:desktop"]["level"] == "warn" and "cua_driver" in rows["backend:desktop"]["detail"]
    piper = {r["check"]: r for r in _backend_checks(Settings(home=home, backends={"tts": "piper"}), h)}
    assert "[gpl-isolated]" in piper["backend:tts"]["detail"]
