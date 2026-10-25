"""W2-C: sensor probes (fake lifecycle, session-scoped registry, RTSP credential redaction,
honest checks for uvc/rtsp/ble/rtc) and the `sensor_read` tool."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from auoreo.core.permissions import PermissionEngine
from auoreo.core.redaction import redact
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.perception import rtc
from auoreo.perception.sensors import redact_source, registry_for
from auoreo.tools.context import ToolError
from tests.perception_helpers import call, ctx_for

SECRET_URL = "rtsp://admin:hunter2@cam.local:554/stream1"


def probe(svc: Services, name: str, settings: Settings | None = None) -> Any:
    return svc.harness.backends_for("sensor")[name].load()(settings or svc.settings)


async def _until(cond: Any) -> None:
    for _ in range(150):
        if cond():
            return
        await asyncio.sleep(0.02)


async def test_fake_probe_lifecycle(services: Services) -> None:
    p = probe(services, "fake")
    assert (await p.check()).ok and p.capabilities()["video"] is True
    with pytest.raises(ToolError, match="not started"):
        await p.sample()
    await p.start("script:1,0")
    a, b, c = [await p.sample() for _ in range(3)]
    assert (a.presence, b.presence, c.presence) == (True, False, True)
    assert a.reading["index"] == 0 and b.reading["temperature_c"] == 21.5 and a.confidence == 0.9
    assert a.image is not None and a.image.startswith(b"\x89PNG")
    boxes = await services.backend("detection").detect(a.image)
    assert "person" in [x.label for x in boxes]  # the generated frame feeds detection
    assert "person" not in [x.label for x in await services.backend("detection").detect(b.image or b"")]
    await p.stop()
    with pytest.raises(ToolError):
        await p.sample()
    with pytest.raises(ToolError, match="script"):
        await p.start("script:x")


async def test_sensor_read_tool_is_session_scoped(services: Services, workdir: Path) -> None:
    ctx = ctx_for(services, workdir)
    res = await call(services, ctx, "sensor_read", {"source": "script:1,0", "samples": 3})
    assert not res.is_error, res.content
    assert res.content.splitlines()[0] == "[sensor: fake on script:1,0]"
    assert "sample 1: presence=yes confidence=0.90" in res.content
    assert "sample 2: presence=no" in res.content and "sample 3: presence=yes" in res.content
    assert len(res.images) == 1 and res.meta["capabilities"]["telemetry"] is True
    again = await call(services, ctx, "sensor_read", {"source": "script:1,0", "image": False})
    assert "sample 4: presence=no" in again.content and again.images == []  # same open probe
    other = await call(services, ctx_for(services, workdir, "s2"), "sensor_read", {"source": "script:1,0"})
    assert "sample 1:" in other.content
    reg = registry_for(services)
    assert set(reg.live("s1")) == {("fake", "script:1,0")}
    op = reg.live("s1")[("fake", "script:1,0")]
    await services.emit("s1", "session.archived", {})  # the bus watch stops the probe
    await _until(lambda: not reg.live("s1"))
    assert not reg.live("s1") and op.probe.stopped is True
    assert reg.live("s2")
    await services.emit("server", "session.deleted", {"session_id": "s2"})
    await _until(lambda: not reg.live("s2"))
    assert not reg.live("s2")
    stop = await call(services, ctx, "sensor_read", {"source": "x", "stop": True})
    assert "(probe stopped)" in stop.content and not reg.live("s1")


async def test_server_close_stops_probes(home: Path, workdir: Path, gateway: Any) -> None:
    svc = await Services.create(
        Settings(home=home, gateway="fake"), cwd=workdir, gateway=gateway, with_mcp=False
    )
    await call(svc, ctx_for(svc, workdir), "sensor_read", {"source": "cam"})
    op = registry_for(svc).live("s1")[("fake", "cam")]
    await svc.close()
    assert op.probe.stopped is True


async def test_sensor_read_asks_by_default(services: Services, workdir: Path) -> None:
    ctx = ctx_for(services, workdir)
    ctx.services["engine"] = PermissionEngine([])
    res = await call(services, ctx, "sensor_read", {"source": "0"})
    assert res.is_error and "needs approval" in res.content


def test_redact_source() -> None:
    assert redact_source(SECRET_URL) == "rtsp://***@cam.local:554/stream1"
    assert redact_source("rtsp://token@cam/x") == "rtsp://***@cam/x"
    assert redact_source("rtsp://cam/x") == "rtsp://cam/x" and redact_source("0") == "0"
    assert "hunter2" not in redact(SECRET_URL)  # the core redactor masks DSN-style userinfo too


async def test_rtsp_credentials_never_logged_or_audited(
    services: Services, workdir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rtsp = probe(services, "rtsp")
    assert rtsp.capabilities()["ptz"] is False  # read-only
    with pytest.raises(ToolError) as bad:
        rtsp.target("http://admin:hunter2@cam/x")
    assert "hunter2" not in str(bad.value) and "***@cam" in str(bad.value)
    assert rtsp.target(SECRET_URL) == SECRET_URL
    st = await rtsp.check()
    assert not st.ok and "cv2" in st.detail and "opencv" in st.remedy  # honest: not installed
    caplog.set_level(logging.INFO, logger="auoreo")
    res = await call(
        services, ctx_for(services, workdir), "sensor_read", {"probe": "rtsp", "source": SECRET_URL}
    )
    assert res.is_error and "unavailable" in res.content and "hunter2" not in res.content
    # the fake probe accepts any source: start/stop audit + logs must carry the redacted URL
    res = await call(
        services, ctx_for(services, workdir), "sensor_read", {"source": SECRET_URL, "stop": True}
    )
    assert not res.is_error and "[sensor: fake on rtsp://***@cam.local:554/stream1]" in res.content
    assert "hunter2" not in res.content
    assert "hunter2" not in caplog.text and "***@cam.local" in caplog.text
    con = sqlite3.connect(services.settings.db_path)
    try:
        dump = "\n".join(con.iterdump())
        audit = con.execute("SELECT action, detail FROM audit WHERE action LIKE 'sensor.%'").fetchall()
    finally:
        con.close()
    assert [a for a, _ in audit] == ["sensor.start", "sensor.stop"]
    assert "***@cam.local" in audit[0][1]
    assert "hunter2" not in dump  # events (tool args), audit and transcript


async def test_uvc_ble_rtc_checks_are_honest(services: Services, home: Path) -> None:
    uvc = probe(services, "uvc")
    st = await uvc.check()
    assert not st.ok and "cv2" in st.detail
    assert uvc.target("0") == 0 and uvc.target("/dev/video2") == 2
    with pytest.raises(ToolError, match="device index"):
        uvc.target("rtsp://cam")
    ble = probe(services, "ble")
    st = await ble.check()
    assert not st.ok and "stub" in st.detail and "mqtt" in st.remedy
    with pytest.raises(ToolError, match="stub"):
        await ble.start("aa:bb")
    off = probe(services, "rtc")
    assert not (await off.check()).ok
    on = probe(services, "rtc", Settings(home=home, perception_sample_rtc_video=True))
    assert (await on.check()).ok
    await on.start("sess-rtc")
    assert (await on.sample()).presence is False
    rtc.put("sess-rtc", b"\xff\xd8\xff fake jpeg")
    s = await on.sample()
    assert s.presence is True and s.image == b"\xff\xd8\xff fake jpeg"
    rtc.forget("sess-rtc")
    assert rtc.latest("sess-rtc") is None
    assert registry_for(services).default_probe() == "fake"
    with pytest.raises(ToolError, match="unknown sensor probe"):
        await registry_for(services).acquire("s1", "nope", "0")


async def test_rtc_sample_video_keeps_one_frame_per_interval() -> None:
    from PIL import Image

    class Frame:
        def to_image(self) -> Image.Image:
            return Image.new("RGB", (64, 48), (10, 200, 10))

    class Track:
        def __init__(self, n: int) -> None:
            self.n = n

        async def recv(self) -> Frame:
            if self.n == 0:
                raise RuntimeError("MediaStreamError")
            self.n -= 1
            return Frame()

    puts: list[str] = []
    real_put = rtc.put
    try:
        rtc.put = lambda sid, jpeg, now=None: (puts.append(sid), real_put(sid, jpeg, now))  # type: ignore[assignment]
        await rtc.sample_video(Track(5), "sess-v", interval_s=3600)  # ends cleanly at track end
    finally:
        rtc.put = real_put  # type: ignore[assignment]
    assert puts == ["sess-v"]  # one frame per interval, the rest are drained
    got = rtc.latest("sess-v")
    assert got is not None and got[1].startswith(b"\xff\xd8\xff")
    rtc.forget("sess-v")
