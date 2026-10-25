"""F5 call transfer on the livekit backend (fake server API + fake realtime room): cold REFER with
`play_dialtone`, warm transfer in-room (self-hosted) and via consult room + MoveParticipant
(Cloud), Krisp / region on the dial request, auditing, and the `call_transfer` tool (refuses on
a non-livekit transport; real audit rows through the executor)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from auoreo.core.db import AuditRow
from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.tools.context import ToolContext, ToolResult
from tests.unit.test_livekit_dtmf import FakeRoom, make
from tests.unit.test_rtc_transport import FakeLiveKitAPI, backend_cls

pytest.importorskip("livekit.api")


@pytest.fixture
def lk_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEKIT_API_KEY", "APIkey123")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "s" * 40)


SIP_CFG = {"outbound_trunk_id": "ST_out", "krisp_enabled": True}


def methods(log: list[tuple[str, Any]]) -> list[str]:
    return [m for m, _ in log if m != "aclose"]


def req(log: list[tuple[str, Any]], method: str) -> Any:
    return next(r for m, r in log if m == method)


async def test_cold_transfer_play_dialtone_and_audit(home: Path, lk_env: None) -> None:
    be, log, room, svc = make(home)
    await be.connect_agent("auoreo-call-1")
    await be.transfer("auoreo-call-1", "sip_caller", "tel:+15550100", play_dialtone=True)
    r = req(log, "transfer_sip_participant")
    assert (
        r.play_dialtone is True
        and r.transfer_to == "tel:+15550100"
        and r.participant_identity == "sip_caller"
    )
    ev = next(d for _, t, d in svc.events if t == "rtc.transfer")
    assert ev["kind"] == "cold" and ev["target"] == "****0100" and "+1555" not in json.dumps(ev)
    assert svc.audits[-1]["action"] == "rtc.transfer" and svc.audits[-1]["session_id"] == "ses_1"
    assert room.disconnected  # the agent leaves after handing off


async def test_warm_transfer_self_hosted_is_in_room(home: Path, lk_env: None) -> None:
    from livekit.protocol import sip

    be, log, room, svc = make(home, sip=SIP_CFG, region="eu-central")
    out = await be.warm_transfer(
        "auoreo-call-1", "sip_caller", "+15550100", announce_text="Caller asks about invoice 42"
    )
    assert methods(log) == ["create_sip_participant", "remove_participant"]
    dial = req(log, "create_sip_participant")
    assert dial.sip_trunk_id == "ST_out" and dial.sip_call_to == "+15550100"
    assert dial.room_name == "auoreo-call-1" and dial.participant_identity == "transfer-target"
    assert dial.wait_until_answered and dial.krisp_enabled and dial.destination.region == "eu-central"
    assert dial.media_encryption == sip.SIPMediaEncryption.SIP_MEDIA_ENCRYPT_REQUIRE
    assert dial.ringing_timeout.seconds == 30
    assert req(log, "remove_participant").identity == "auoreo-agent"
    assert out == {
        "kind": "warm",
        "caller": "sip_caller",
        "target": "****0100",
        "bridge": "in_room",
        "announced": True,
        "consult_room": None,
    }
    assert room.played and room.disconnected
    assert [t for _, t, _ in svc.events][-1] == "rtc.transfer"
    assert svc.audits[-1]["action"] == "rtc.transfer" and svc.audits[-1]["detail"]["kind"] == "warm"


async def test_warm_transfer_cloud_uses_consult_room_and_move(home: Path, lk_env: None) -> None:
    be, log, _, _ = make(home, url="wss://proj.livekit.cloud", cloud_consent=True, sip=SIP_CFG)
    out = await be.warm_transfer("auoreo-call-1", "sip_caller", "tel:+15550100")
    assert methods(log) == [
        "create_room",
        "create_sip_participant",
        "move_participant",
        "delete_room",
        "remove_participant",
    ]
    consult = out["consult_room"]
    assert (
        consult.startswith("auoreo-call-1-consult-")
        and req(log, "create_sip_participant").room_name == consult
    )
    move = req(log, "move_participant")
    assert (move.room, move.identity, move.destination_room) == (consult, "transfer-target", "auoreo-call-1")
    assert out["bridge"] == "move" and out["announced"] is False  # nothing to announce


async def test_warm_transfer_needs_an_outbound_trunk(home: Path, lk_env: None) -> None:
    be, log, _, _ = make(home)
    with pytest.raises(ValueError, match="outbound_trunk_id"):
        await be.warm_transfer("auoreo-call-1", "sip_caller", "+15550100")
    with pytest.raises(ValueError, match="E.164"):
        be.tel_uri("5550100")
    assert methods(log) == []


def test_krisp_and_region_pass_through(home: Path, lk_env: None) -> None:
    be, *_ = make(home, sip={"krisp_enabled": True, "outbound_trunk_id": "ST"}, region="us-west")
    assert be.inbound_trunk_request(["+15550100"], auth_username="u", auth_password="p").trunk.krisp_enabled
    dial = be.sip_participant_request("r", "+15550100")
    assert dial.krisp_enabled and dial.destination.region == "us-west"
    off, *_ = make(home, sip={"outbound_trunk_id": "ST"})
    dial = off.sip_participant_request("r", "+15550100")
    assert not dial.krisp_enabled and not dial.HasField("destination")


# --------------------------------------------------------------- the tool
@pytest.fixture
async def services(home: Path, workdir: Path, gateway: FakeGateway) -> AsyncIterator[Services]:
    st = Settings(home=home, gateway="fake", approval_timeout_s=2)
    svc = await Services.create(st, cwd=workdir, gateway=gateway, with_mcp=False)
    try:
        yield svc
    finally:
        await svc.close()


async def run_tool(svc: Services, workdir: Path, args: dict[str, Any]) -> ToolResult:
    engine = PermissionEngine([Rule(permission="telephony", pattern="*", action="allow")])
    ctx = ToolContext(
        session_id="ses_1",
        cwd=workdir,
        home=svc.settings.home,
        harness=svc.harness,
        interactive=False,
        services={"services": svc, "engine": engine},
    )
    out = await svc.coordinator.runner.executor.run(
        ToolCall("c1", "call_transfer", json.dumps(args)), tools=svc.harness.tools, ctx=ctx, engine=engine
    )
    return out.result


def test_tool_manifest(services: Services) -> None:
    m = services.harness.tools["call_transfer"].manifest
    assert (m.permission_name, m.default_action, m.exposure, m.is_destructive) == (
        "telephony",
        "ask",
        "deferred",
        True,
    )


async def test_tool_refuses_without_livekit(services: Services, workdir: Path) -> None:
    res = await run_tool(
        services, workdir, {"kind": "cold", "target": "+15550100", "caller": "c", "room": "r"}
    )
    assert res.is_error and "needs the livekit rtc transport (selected: aiortc)" in res.content


async def test_tool_cold_and_warm_through_the_executor(
    services: Services, workdir: Path, lk_env: None
) -> None:
    log: list[tuple[str, Any]] = []
    room = FakeRoom()
    services.settings.livekit = services.settings.livekit.model_copy(
        update={
            "url": "wss://lk.example.com",
            "sip": services.settings.livekit.sip.model_copy(update=SIP_CFG),
        }
    )
    be = backend_cls(services.settings.home, "livekit")(
        services.settings, api_factory=lambda: FakeLiveKitAPI(log), rtc_factory=lambda: room
    )
    be.rooms["auoreo-ses_1"] = {"session_id": "ses_1"}
    services._backends["rtc_transport"] = be
    res = await run_tool(services, workdir, {"kind": "cold", "target": "+15550100", "caller": "sip_caller"})
    assert not res.is_error, res.content
    assert req(log, "transfer_sip_participant").room_name == "auoreo-ses_1"
    assert req(log, "transfer_sip_participant").play_dialtone is True
    res = await run_tool(services, workdir, {"kind": "warm", "target": "+15550199", "caller": "sip_caller"})
    assert not res.is_error and "in_room" in res.content, res.content
    bad = await run_tool(services, workdir, {"kind": "cold", "target": "12345", "caller": "x"})
    assert bad.is_error and "E.164" in bad.content
    async with services.db.session() as s:
        rows = [r for r in (await s.execute(select(AuditRow))).scalars() if r.action == "rtc.transfer"]
    assert len(rows) == 2 and all(r.session_id == "ses_1" for r in rows)
