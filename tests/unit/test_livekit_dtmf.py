"""F5 SIP DTMF on the livekit backend: `sip_dtmf_received` from a fake `livekit.rtc` room →
`rtc.dtmf` events + the digit → action IVR; outbound tones via `publish_dtmf`. The LiveKit
server API is faked (no server here); protobuf requests are built for real."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from auoreo.core.settings import Settings
from tests.audio import tone_wav
from tests.unit.test_rtc_transport import FakeLiveKitAPI, backend_cls

pytest.importorskip("livekit.api")
TEL = "auoreo_backend_rtc_transport_livekit_telephony"


class FakeLocal:
    def __init__(self) -> None:
        self.tones: list[tuple[int, str]] = []

    async def publish_dtmf(self, *, code: int, digit: str) -> None:
        self.tones.append((code, digit))


class FakeRoom:
    """The slice of `livekit.rtc.Room` the backend uses."""

    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}
        self.connected: tuple[str, str] | None = None
        self.disconnected = False
        self.local_participant = FakeLocal()
        self.played: list[tuple[int, int, int]] = []

    def on(self, event: str, cb: Any) -> None:
        self.handlers[event] = cb

    async def connect(self, url: str, token: str) -> None:
        self.connected = (url, token)

    async def disconnect(self) -> None:
        self.disconnected = True

    async def play_pcm(self, pcm: bytes, rate: int, channels: int) -> None:
        self.played.append((len(pcm), rate, channels))

    def press(self, digit: str, who: str = "sip_caller") -> None:
        ev = SimpleNamespace(code=0, digit=digit, participant=SimpleNamespace(identity=who))
        self.handlers["sip_dtmf_received"](ev)


class FakeSvc:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []
        self.audits: list[dict[str, Any]] = []
        self.submitted: list[tuple[str, str]] = []
        self.audit = SimpleNamespace(record=self._record)
        self.coordinator = SimpleNamespace(submit=self._submit)
        self.ext = {"voice": SimpleNamespace(speak=self._speak)}

    async def emit(self, agg: str, type_: str, data: dict[str, Any]) -> None:
        self.events.append((agg, type_, data))

    async def _record(self, **kw: Any) -> None:
        self.audits.append(kw)

    async def _submit(self, sid: str, text: str) -> None:
        self.submitted.append((sid, text))

    async def _speak(self, text: str, **_: Any) -> Any:
        return SimpleNamespace(data=tone_wav(0.2), media_type="audio/wav")


@pytest.fixture
def lk_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEKIT_API_KEY", "APIkey123")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "s" * 40)


def make(home: Path, **cfg: Any) -> tuple[Any, list[tuple[str, Any]], FakeRoom, FakeSvc]:
    log: list[tuple[str, Any]] = []
    room = FakeRoom()
    st = Settings(home=home, livekit={"url": "wss://lk.example.com", **cfg})
    be = backend_cls(home, "livekit")(st, api_factory=lambda: FakeLiveKitAPI(log), rtc_factory=lambda: room)
    svc = FakeSvc()
    be.bind(svc)
    be.rooms["auoreo-call-1"] = {"session_id": "ses_1", "room": "auoreo-call-1"}
    sys.modules[TEL].TONE_GAP_S = 0
    return be, log, room, svc


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


IVR = {
    "1": "say:Opening hours are nine to five",
    "0": "transfer:+15550100",
    "9": "hangup",
    "5": "followup:wants a callback",
}


async def test_dtmf_events_and_agent_token(home: Path, lk_env: None) -> None:
    import jwt

    be, _, room, svc = make(home)
    await be.connect_agent("auoreo-call-1")
    url, token = room.connected or ("", "")
    claims = jwt.decode(token, "s" * 40, algorithms=["HS256"])
    assert url == "wss://lk.example.com" and claims["sub"] == "auoreo-agent" and claims["kind"] == "agent"
    assert claims["video"]["room"] == "auoreo-call-1"
    room.press("7")
    await settle()
    assert svc.events == [
        (
            "ses_1",
            "rtc.dtmf",
            {"room": "auoreo-call-1", "participant": "sip_caller", "digit": "7", "action": None},
        )
    ]
    assert await be.connect_agent("auoreo-call-1") is room  # one link per room


async def test_ivr_actions(home: Path, lk_env: None) -> None:
    from livekit.protocol import sip

    be, log, room, svc = make(home, sip={"ivr": IVR})
    await be.connect_agent("auoreo-call-1")
    for d in "1590":
        room.press(d)
        await settle()
    types = [t for _, t, _ in svc.events]
    assert types.count("rtc.dtmf") == 4 and "rtc.said" in types and "rtc.transfer" in types
    assert room.played and room.played[0][1] == 16000  # TTS played into the room
    calls = [(m, r) for m, r in log if m != "aclose"]
    assert [m for m, _ in calls] == ["remove_participant", "transfer_sip_participant"]
    assert calls[0][1].identity == "sip_caller" and calls[0][1].room == "auoreo-call-1"
    xfer = calls[1][1]
    assert isinstance(xfer, sip.TransferSIPParticipantRequest)
    assert xfer.transfer_to == "tel:+15550100" and xfer.play_dialtone is True
    assert svc.submitted == [("ses_1", "[caller sip_caller pressed 5] wants a callback")]
    assert {a["action"] for a in svc.audits} == {"rtc.hangup", "rtc.transfer"}


async def test_ivr_failure_is_an_event_not_a_crash(home: Path, lk_env: None) -> None:
    be, _, room, svc = make(home, sip={"ivr": {"5": "followup:x"}})
    be.rooms.clear()  # room not bound to a session → followup cannot be delivered
    await be.connect_agent("auoreo-call-2")
    room.press("5")
    await settle()
    assert [t for _, t, _ in svc.events] == ["rtc.dtmf", "rtc.ivr_failed"]


def test_ivr_settings_are_validated(home: Path) -> None:
    ok = Settings(home=home, livekit={"sip": {"ivr": IVR}})
    assert ok.livekit.sip.ivr["9"] == "hangup"
    for bad in ({"12": "hangup"}, {"x": "hangup"}, {"1": "dial:+1"}, {"1": "say:"}):
        with pytest.raises(ValidationError):
            Settings(home=home, livekit={"sip": {"ivr": bad}})


async def test_send_dtmf_publishes_rfc4733_codes(home: Path, lk_env: None) -> None:
    be, _, room, svc = make(home)
    assert await be.send_dtmf("auoreo-call-1", "123#*") == 5
    assert room.local_participant.tones == [(1, "1"), (2, "2"), (3, "3"), (11, "#"), (10, "*")]
    assert svc.audits[-1]["action"] == "rtc.dtmf_sent" and "123" not in str(svc.audits[-1])  # no PINs
    for bad in ("", "12x", "1;2"):
        with pytest.raises(ValueError, match="DTMF digits"):
            await be.send_dtmf("auoreo-call-1", bad)


async def test_without_the_realtime_sdk(home: Path, lk_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    be, *_ = make(home)
    be.rtc_factory = None
    import livekit

    monkeypatch.setitem(sys.modules, "livekit.rtc", None)  # import fails
    monkeypatch.delattr(livekit, "rtc", raising=False)
    with pytest.raises(RuntimeError, match="realtime SDK is not installed"):
        await be.send_dtmf("auoreo-call-1", "1")
    assert await be.say("auoreo-call-1", "hello") is False  # announcement skipped, not an error


async def test_leave_disconnects_the_agent(home: Path, lk_env: None) -> None:
    be, _, room, _ = make(home)
    await be.connect_agent("auoreo-call-1")
    await be.leave("auoreo-call-1")
    assert room.disconnected and "auoreo-call-1" not in be.links
