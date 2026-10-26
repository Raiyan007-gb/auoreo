"""`rtc_transport` backends: the aiortc wrapper delegates to `RtcManager`; the LiveKit backend's
token grants (real `livekit-api` + PyJWT), Cloud consent gate, SIP numbers rule and egress
request shape (protobufs built for real, the server API faked); doctor ICE/STUN rows."""

from __future__ import annotations

import asyncio
import socket
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from auoreo.backends.rtc_transport import RtcTransport
from auoreo.core import diagnostics
from auoreo.core.settings import Settings
from auoreo.harness.discovery import default_roots
from auoreo.harness.registry import Harness

REPO = Path(__file__).resolve().parents[2]
lk_api = pytest.importorskip("livekit.api")


def backend_cls(home: Path, name: str) -> Any:
    h = Harness(default_roots(home, home, REPO, []))
    bdef = h.select_backend("rtc_transport", name)
    assert bdef is not None
    return bdef.load()


# ------------------------------------------------------------------- aiortc
class FakeManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def offer(self, sdp: str, type_: str, session_id: str | None) -> tuple[str, str]:
        self.calls.append(("offer", (sdp, type_, session_id)))
        return "rtc_abc", "v=0 answer"

    async def close(self, rtc_id: str) -> bool:
        self.calls.append(("close", rtc_id))
        return True

    def describe(self) -> list[dict[str, Any]]:
        return [{"rtc_id": "rtc_abc", "session_id": "s1", "state": "new", "created_at": datetime.now(UTC)}]


def test_aiortc_is_the_default_transport(home: Path) -> None:
    h = Harness(default_roots(home, home, REPO, []))
    assert h.select_backend("rtc_transport", None).manifest.name == "aiortc"  # type: ignore[union-attr]
    assert set(h.backends_for("rtc_transport")) >= {"aiortc", "livekit"}


async def test_aiortc_wrapper_delegates_to_rtc_manager(home: Path) -> None:
    be = backend_cls(home, "aiortc")(Settings(home=home))
    assert isinstance(be, RtcTransport)
    with pytest.raises(RuntimeError, match="not bound"):
        await be.join(session_id=None, identity="u", offer_sdp="v=0")
    mgr = FakeManager()
    be.bind(SimpleNamespace(rtc=mgr, ext={}))
    info = await be.join(session_id="s1", identity="u", offer_sdp="v=0 offer")
    assert (info.transport, info.session_id, info.sdp, info.extra["join_id"]) == (
        "aiortc",
        "s1",
        "v=0 answer",
        "rtc_abc",
    )
    with pytest.raises(ValueError, match="SDP offer"):
        await be.join(session_id="s1", identity="u", offer_sdp=None)
    await be.leave("rtc_abc")
    assert mgr.calls == [("offer", ("v=0 offer", "offer", "s1")), ("close", "rtc_abc")]
    assert [p["rtc_id"] for p in await be.describe()] == ["rtc_abc"]
    status = await be.check()
    assert status.ok and "1 ICE server(s), 0 TURN" in status.detail


# ------------------------------------------------------------------ livekit
@pytest.fixture
def lk_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEKIT_API_KEY", "APIkey123")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "s" * 40)


class FakeService:
    def __init__(self, log: list[tuple[str, Any]], fail: bool = False) -> None:
        self.log, self.fail = log, fail

    def __getattr__(self, method: str) -> Any:
        async def call(req: Any) -> Any:
            if self.fail:
                raise OSError("connection refused")
            self.log.append((method, req))
            return SimpleNamespace(sip_trunk_id="ST_1", sip_dispatch_rule_id="SDR_1", egress_id="EG_1")

        return call


class FakeLiveKitAPI:
    def __init__(self, log: list[tuple[str, Any]], fail: bool = False) -> None:
        self.room = self.sip = self.egress = FakeService(log, fail)
        self.log = log

    async def aclose(self) -> None:
        self.log.append(("aclose", None))


def livekit(home: Path, *, fail: bool = False, **cfg: Any) -> tuple[Any, list[tuple[str, Any]]]:
    log: list[tuple[str, Any]] = []
    st = Settings(home=home, livekit={"url": "wss://lk.example.com", **cfg})
    be = backend_cls(home, "livekit")(st, api_factory=lambda: FakeLiveKitAPI(log, fail))
    return be, log


def test_token_is_room_scoped_with_one_hour_ttl(home: Path, lk_env: None) -> None:
    import jwt

    be, _ = livekit(home)
    token = be.mint_token("alice", "auoreo-s1")
    claims = jwt.decode(token, "s" * 40, algorithms=["HS256"])
    assert claims["sub"] == "alice" and claims["iss"] == "APIkey123"
    assert claims["video"]["roomJoin"] is True and claims["video"]["room"] == "auoreo-s1"
    assert not claims["video"].get("roomAdmin") and not claims["video"].get("roomCreate")
    assert claims["exp"] - claims["nbf"] == 600  # I1 §26.9: default 600 s, max 900


async def test_join_creates_the_room_and_returns_a_token(home: Path, lk_env: None) -> None:
    import jwt

    be, log = livekit(home)
    info = await be.join(session_id="ses_01", identity="bob@example.com", offer_sdp=None)
    room = info.room
    assert room is not None and str(uuid.UUID(room)) == room and "ses_01" not in room  # opaque uuid4
    assert (info.transport, info.url) == ("livekit", "wss://lk.example.com")
    claims = jwt.decode(info.token, "s" * 40, algorithms=["HS256"])
    assert claims["video"]["room"] == room
    assert claims["sub"] == info.extra["identity"] and str(uuid.UUID(claims["sub"])) == claims["sub"]
    assert "bob" not in info.token and claims["exp"] - claims["nbf"] <= 900
    assert log[0][0] == "create_room" and log[0][1].name == room and log[1] == ("aclose", None)
    again = await be.join(session_id="ses_01", identity="u", offer_sdp=None)
    assert again.room == room and again.extra["identity"] != info.extra["identity"]
    rooms = await be.describe()
    assert rooms[0]["room"] == room and "token" not in rooms[0]
    await be.leave(room)
    assert log[-2][0] == "delete_room" and log[-2][1].room == room
    assert await be.describe() == []


async def test_cloud_url_needs_consent(home: Path, lk_env: None) -> None:
    be, log = livekit(home, url="wss://proj.livekit.cloud")
    status = await be.check()
    assert not status.ok and "cloud_consent" in status.detail
    with pytest.raises(PermissionError, match="LiveKit Cloud"):
        await be.join(session_id="s", identity="u", offer_sdp=None)
    assert log == []  # nothing was sent before the gate
    ok, _ = livekit(home, url="wss://proj.livekit.cloud", cloud_consent=True)
    assert (await ok.check()).ok
    assert (await ok.join(session_id="s", identity="u", offer_sdp=None)).extra["cloud"] is True


async def test_check_reports_missing_config_and_reachability(
    home: Path, lk_env: None, monkeypatch: pytest.MonkeyPatch, httpx_mock: Any
) -> None:
    cls = backend_cls(home, "livekit")
    assert "url is not set" in (await cls(Settings(home=home)).check()).detail
    be, _ = livekit(home)
    assert "reachability skipped" in (await be.check()).detail
    monkeypatch.delenv("AUOREO_OFFLINE")
    httpx_mock.add_response(url="https://lk.example.com/", text="OK")
    status = await be.check()
    assert status.ok and "self-hosted wss://lk.example.com reachable (HTTP 200)" in status.detail
    monkeypatch.delenv("LIVEKIT_API_SECRET")
    status = await be.check()
    assert not status.ok and "LIVEKIT_API_SECRET" in status.detail


def test_sip_inbound_numbers_rule_and_srtp(home: Path, lk_env: None) -> None:
    from livekit.protocol import sip

    be, _ = livekit(home)
    with pytest.raises(ValueError, match="numbers=\\[\\]"):
        be.inbound_trunk_request([])
    with pytest.raises(ValueError):
        be.inbound_trunk_request([], auth_username="u")  # a username alone is not auth
    req = be.inbound_trunk_request([], allowed_addresses=["203.0.113.0/24"])
    assert list(req.trunk.allowed_addresses) == ["203.0.113.0/24"]
    assert req.trunk.media_encryption == sip.SIPMediaEncryption.SIP_MEDIA_ENCRYPT_REQUIRE
    assert req.trunk.krisp_enabled is False  # F5: off by default
    req = be.inbound_trunk_request(["+15550100"], auth_username="u", auth_password="p")
    assert list(req.trunk.numbers) == ["+15550100"] and req.trunk.auth_username == "u"


async def test_sip_helpers_call_the_sip_service(home: Path, lk_env: None) -> None:
    from livekit.protocol import sip

    be, log = livekit(home)
    assert await be.create_sip_inbound(["+15550100"], allowed_addresses=["198.51.100.7"]) == "ST_1"
    assert await be.create_dispatch_rule(trunk_ids=["ST_1"]) == "SDR_1"
    assert await be.create_sip_outbound("sip.carrier.example", ["+15550100"]) == "ST_1"
    await be.transfer("auoreo-call-x", "caller", "tel:+15550199")
    methods = [m for m, _ in log if m != "aclose"]
    assert methods == [
        "create_sip_inbound_trunk",
        "create_sip_dispatch_rule",
        "create_sip_outbound_trunk",
        "transfer_sip_participant",
    ]
    rule = log[2][1]
    assert rule.rule.WhichOneof("rule") == "dispatch_rule_individual"
    assert rule.rule.dispatch_rule_individual.room_prefix == "auoreo-call-" and list(rule.trunk_ids) == [
        "ST_1"
    ]
    out = log[4][1].trunk
    assert out.transport == sip.SIPTransport.SIP_TRANSPORT_TLS
    assert out.media_encryption == sip.SIPMediaEncryption.SIP_MEDIA_ENCRYPT_REQUIRE
    with pytest.raises(ValueError, match="tel: or sip:"):
        await be.transfer("r", "p", "+1555")


async def test_audio_egress_is_start_egress_with_audio_only_template(home: Path, lk_env: None) -> None:
    be, log = livekit(home)
    assert await be.start_audio_egress("auoreo-s1") == "EG_1"
    method, req = log[0]
    assert method == "start_egress"
    assert req.room_name == "auoreo-s1" and req.WhichOneof("source") == "template"
    assert req.template.audio_only is True and not req.template.video_only
    assert req.outputs[0].file.filepath == "auoreo-s1/{time}.ogg"


async def test_api_failures_become_connection_errors(home: Path, lk_env: None) -> None:
    be, log = livekit(home, fail=True)
    with pytest.raises(ConnectionError, match="LiveKit API call failed"):
        await be.join(session_id="s", identity="u", offer_sdp=None)
    assert log == [("aclose", None)]


def test_subscribable_skips_egress_ingress_and_agents(home: Path) -> None:
    from livekit.protocol import models

    mod = backend_cls(home, "livekit")
    import sys

    impl = sys.modules[mod.__module__]
    kinds = models.ParticipantInfo.Kind
    people = [
        models.ParticipantInfo(identity="alice", kind=kinds.STANDARD),
        models.ParticipantInfo(identity="rec", kind=kinds.EGRESS),
        models.ParticipantInfo(identity="rtmp", kind=kinds.INGRESS),
        models.ParticipantInfo(identity="bot", kind=kinds.AGENT),
        models.ParticipantInfo(identity="phone", kind=kinds.SIP),
        {"identity": "dict-agent", "kind": "agent"},
    ]
    assert [p.identity for p in impl.subscribable(people)] == ["alice", "phone"]


# ------------------------------------------------------------------ doctor rows
def test_stun_target_parsing() -> None:
    assert diagnostics.stun_target("stun:stun.l.google.com:19302") == ("stun.l.google.com", 19302)
    assert diagnostics.stun_target("stun:example.org") == ("example.org", 3478)
    assert diagnostics.stun_target("turn:t.example.org:3478?transport=udp") is None
    assert diagnostics.stun_target("stun:host:notaport") is None


def fake_stun_server() -> tuple[int, threading.Thread]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))

    def serve() -> None:
        data, addr = sock.recvfrom(2048)
        port = addr[1] ^ 0x2112
        ip = bytes(b ^ m for b, m in zip(socket.inet_aton(addr[0]), b"\x21\x12\xa4\x42", strict=True))
        attr = b"\x00\x20\x00\x08\x00\x01" + port.to_bytes(2, "big") + ip
        sock.sendto(b"\x01\x01" + len(attr).to_bytes(2, "big") + data[4:20] + attr, addr)
        sock.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return sock.getsockname()[1], t


async def test_stun_binding_against_a_local_server() -> None:
    port, t = fake_stun_server()
    mapped = await asyncio.to_thread(diagnostics.stun_binding, "127.0.0.1", port)
    assert mapped.startswith("127.0.0.1:")
    t.join(2)


async def test_rtc_doctor_rows(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = {c.check: c for c in await diagnostics.rtc_checks(Settings(home=home))}
    assert rows["rtc:ice"].level == "ok" and "1 STUN, 0 TURN" in rows["rtc:ice"].detail
    assert rows["rtc:stun"].detail.startswith("skipped (AUOREO_OFFLINE)")
    assert rows["rtc:turn"].level == "warn" and "CGNAT" in rows["rtc:turn"].remedy
    assert rows["rtc:transport"].detail.startswith("aiortc: ")

    port, t = fake_stun_server()
    monkeypatch.delenv("AUOREO_OFFLINE")
    st = Settings(home=home, rtc_ice_servers=[f"stun:127.0.0.1:{port}", "turns:turn.example.org:443"])
    rows = {c.check: c for c in await diagnostics.rtc_checks(st)}
    assert rows["rtc:stun"].level == "ok" and "→ public 127.0.0.1:" in rows["rtc:stun"].detail
    assert rows["rtc:turn"].level == "ok"
    t.join(2)

    st = Settings(home=home, rtc_ice_servers=[], backends={"rtc_transport": "livekit"})
    rows = {c.check: c for c in await diagnostics.rtc_checks(st)}
    assert rows["rtc:ice"].level == "warn" and rows["rtc:stun"].level == "warn"
    assert (
        rows["rtc:transport"].level == "warn"
        and "livekit: settings.livekit.url" in rows["rtc:transport"].detail
    )
    assert diagnostics.rtc_checks in diagnostics.EXTRA_CHECKS
