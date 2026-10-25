"""`auoreo-events` protocol against a fake data channel (no aiortc needed)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from auoreo.core.services import Services
from auoreo.rtc.audio import LevelMeter
from auoreo.rtc.events import MAX_MESSAGE_BYTES, EventChannel
from auoreo.rtc.peers import RtcManager, RtcUnavailable, SdpTooLarge, redact_sdp


class FakeChannel:
    def __init__(self) -> None:
        self.readyState = "open"
        self.bufferedAmount = 0
        self.sent: list[dict[str, Any]] = []

    def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    def types(self) -> list[str]:
        return [f["type"] for f in self.sent]


async def until(pred: Any, timeout: float = 5) -> None:
    async with asyncio.timeout(timeout):
        while not pred():
            await asyncio.sleep(0.01)


@pytest.fixture
async def chan(services: Services) -> Any:
    fake = FakeChannel()
    closes: list[str] = []

    async def request_close(reason: str) -> None:
        closes.append(reason)

    ec = EventChannel(services, "rtc_test", fake, request_close, soft_limit=1000, drain_timeout_s=0.2)
    await ec.start()
    try:
        yield ec, fake, closes
    finally:
        await ec.stop()


async def test_connected_ping_and_rejections(chan: Any) -> None:
    ec, fake, _ = chan
    assert fake.sent[0]["type"] == "connected" and fake.sent[0]["rtc_id"] == "rtc_test"
    ec.feed(json.dumps({"type": "ping", "tick": 3}))
    ec.feed(json.dumps({"type": "control_request", "request_id": "r", "request": {}}))
    ec.feed(b"\x00binary")
    ec.feed("not json")
    ec.feed("x" * (MAX_MESSAGE_BYTES + 1))
    ec.feed(json.dumps({"type": "subscribe"}))
    await until(lambda: len(fake.sent) >= 7)
    assert fake.sent[1] == {"type": "pong", "tick": 3}
    subtypes = [f["subtype"] for f in fake.sent[2:]]
    assert subtypes == ["unsupported", "binary_dropped", "invalid_frame", "too_large", "invalid_frame"]


async def test_subscribe_replays_then_tails(chan: Any, services: Services) -> None:
    ec, fake, _ = chan
    sess = await services.create_session(title="t")
    ec.feed(json.dumps({"type": "subscribe", "channels": [f"session:{sess.id}"], "last_seq": 0}))
    await until(lambda: "session.created" in fake.types())
    await services.emit(sess.id, "note.added", {"n": 1})
    await services.emit("other", "note.added", {"n": 2})
    await until(lambda: fake.types().count("note.added") == 1)
    await asyncio.sleep(0.05)
    events = [f for f in fake.sent if "seq" in f]
    assert [e["aggregate_id"] for e in events] == [sess.id, sess.id]


async def test_backpressure_drops_coalescable_then_closes(chan: Any, services: Services) -> None:
    ec, fake, closes = chan
    sess = await services.create_session(title="t")
    ec.feed(json.dumps({"type": "subscribe", "channels": [f"session:{sess.id}"]}))
    await until(lambda: ec.sub is not None)
    fake.bufferedAmount = 10_000  # over the soft limit and never draining
    await services.emit(sess.id, "message.delta", {"text": "x"})
    await until(lambda: ec.dropped == 1)
    await services.emit(sess.id, "run.completed", {"text": "done"})
    await until(lambda: closes == ["backpressure"])
    assert "message.delta" not in fake.types() and "run.completed" not in fake.types()


async def test_oversized_event_becomes_an_error_frame(chan: Any, services: Services) -> None:
    ec, fake, _ = chan
    sess = await services.create_session(title="t")
    ec.feed(json.dumps({"type": "subscribe", "channels": [f"session:{sess.id}"]}))
    await until(lambda: ec.sub is not None)
    await services.events.append(sess.id, "tool.completed", {"output": "y" * (MAX_MESSAGE_BYTES + 10)})
    await until(lambda: "error" in fake.types())
    err = fake.sent[-1]
    assert err["subtype"] == "too_large" and err["seq"] > 0


def test_level_meter_windows() -> None:
    np = pytest.importorskip("numpy")
    meter = LevelMeter(interval_s=0.1, rate=16_000)
    tone = (0.5 * 32767 * np.sin(np.arange(1600) / 5)).astype(np.int16)
    meter.add(tone[:800])
    assert not meter.ready(now=10.0)
    meter.add(tone[800:])
    assert meter.ready(now=10.0)
    assert 0.3 < meter.flush(now=10.0) < 0.4
    meter.add(np.zeros(1600, dtype=np.int16))
    assert not meter.ready(now=10.05)  # spacing: at most one level per interval
    assert meter.ready(now=10.2) and meter.flush(now=10.2) == 0.0


async def test_manager_limits_before_aiortc(services: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = RtcManager(services)
    with pytest.raises(SdpTooLarge):
        await mgr.offer("v=0\n" + "a" * 70_000)
    import sys

    monkeypatch.setitem(sys.modules, "aiortc", None)
    with pytest.raises(RtcUnavailable):
        await mgr.offer("v=0\r\nm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n")
    assert await mgr.close("rtc_unknown") is False
    assert redact_sdp("v=0\r\n") == "[REDACTED sdp 5 bytes]"
