"""Instance bridge: two AUOREO instances, signed mail A → B, replay/forgery refused."""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from auoreo.api.app import create_app
from auoreo.core.db import AuditRow, TeamMemberRow
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway
from auoreo.remote import bridge, uds
from auoreo.remote.config import BridgePeer
from tests.remote_helpers import serving
from tests.teams_helpers import call_tool, tool_ctx

SECRET = "shared-bridge-key"


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIDGE_AB", SECRET)


@pytest.fixture
async def svc_b(tmp_path: Path, workdir: Path) -> AsyncIterator[Services]:
    home = tmp_path / "home_b"
    home.mkdir()
    st = Settings(
        home=home,
        gateway="fake",
        instance_name="b",
        bridges={"a": {"url": "http://127.0.0.1:9", "secret": "secret:BRIDGE_AB"}},
    )
    svc = await Services.create(st, cwd=workdir, gateway=FakeGateway(), with_mcp=False)
    try:
        yield svc
    finally:
        await svc.close()


@pytest.fixture
async def b_url(svc_b: Services) -> AsyncIterator[str]:
    async with serving(create_app(svc_b.settings, services=svc_b)) as url:
        yield url


async def _team_with_worker(svc: Services) -> tuple[str, str]:
    """B: a team whose lead has a member named `worker`. Returns (team id, worker session)."""
    teams = svc.ext["teams"]
    lead = (await svc.create_session(title="b lead")).id
    team = await teams.create_team(lead, "b-team")
    worker = (await svc.create_session(title="b worker")).id
    async with svc.db.session() as s:
        s.add(TeamMemberRow(team_id=team.id, name="worker", agent="default", session_id=worker))
        await s.commit()
    return team.id, worker


async def _sender(svc: Services, peer_url: str) -> Any:
    svc.settings.instance_name = "a"
    svc.settings.bridges["b"] = BridgePeer(url=peer_url, secret="secret:BRIDGE_AB")
    lead = (await svc.create_session(title="a lead")).id
    await svc.ext["teams"].create_team(lead, "a-team")
    return tool_ctx(svc, lead)


async def _audit(svc: Services, action: str) -> list[tuple[str, dict[str, Any]]]:
    async with svc.db.session() as s:
        rows = (await s.execute(select(AuditRow).where(AuditRow.action == action))).scalars()
        return [(r.decision, svc.audit.plain_detail(r)) for r in rows]


async def test_send_message_bridges_into_peer_mailbox(
    services: Services, svc_b: Services, b_url: str
) -> None:
    team_b, _ = await _team_with_worker(svc_b)
    ctx = await _sender(services, b_url)
    out, is_error = await call_tool(
        services, ctx, "send_message", {"to": "bridge:b/worker", "message": "ship it", "summary": "go"}
    )
    assert not is_error, out
    assert "delivered to bridge:b/worker" in out

    [mail] = await svc_b.ext["teams"].mail.messages(team_b, "worker")
    assert (mail.sender, mail.recipient, mail.text, mail.summary) == (
        "bridge:a/lead",
        "worker",
        "ship it",
        "go",
    )
    assert [d for d, _ in await _audit(services, "bridge.send")] == ["allow"]
    [(decision, detail)] = await _audit(svc_b, "bridge.receive")
    assert decision == "allow" and detail["sender"] == "a" and detail["team"] == team_b


async def test_unknown_bridge_refused_by_the_tool(services: Services, b_url: str) -> None:
    ctx = await _sender(services, b_url)
    out, is_error = await call_tool(
        services, ctx, "send_message", {"to": "bridge:nope/worker", "message": "x"}
    )
    assert is_error and "unknown bridge 'nope'" in out


def _signed(sender: str = "a", *, ts: int | None = None, key: str = SECRET) -> tuple[bytes, dict[str, str]]:
    ts = int(time.time()) if ts is None else ts
    nonce = secrets.token_urlsafe(18)
    env = {
        "sender": sender,
        "from_member": "lead",
        "to_member": "worker",
        "message": "hi",
        "summary": "",
        "team": None,
        "nonce": nonce,
        "timestamp": ts,
    }
    body = json.dumps(env).encode()
    headers = {
        "content-type": "application/json",
        bridge.SIG_HEADER: bridge.signature(key, ts, nonce, body),
        bridge.TS_HEADER: str(ts),
        bridge.NONCE_HEADER: nonce,
    }
    return body, headers


async def test_inbox_refuses_forgery_replay_staleness_and_strangers(svc_b: Services, b_url: str) -> None:
    await _team_with_worker(svc_b)
    async with httpx.AsyncClient(base_url=b_url) as c:
        body, headers = _signed()
        first = await c.post(bridge.INBOX, content=body, headers=headers)
        assert first.status_code == 200, first.text
        assert first.json()["sender"] == "bridge:a/lead"

        replay = await c.post(bridge.INBOX, content=body, headers=headers)
        assert replay.status_code == 401 and "replayed" in replay.json()["detail"]

        body, headers = _signed(key="wrong-key")
        bad = await c.post(bridge.INBOX, content=body, headers=headers)
        assert bad.status_code == 401 and "signature" in bad.json()["detail"]

        body, headers = _signed()
        tampered = await c.post(bridge.INBOX, content=body.replace(b'"hi"', b'"rm"'), headers=headers)
        assert tampered.status_code == 401

        body, headers = _signed(ts=int(time.time()) - 3600)
        old = await c.post(bridge.INBOX, content=body, headers=headers)
        assert old.status_code == 401 and "window" in old.json()["detail"]

        body, headers = _signed(sender="mallory")
        stranger = await c.post(bridge.INBOX, content=body, headers=headers)
        assert stranger.status_code == 403 and "unknown bridge" in stranger.json()["detail"]

        body, _ = _signed()
        unsigned = await c.post(bridge.INBOX, content=body, headers={"content-type": "application/json"})
        assert unsigned.status_code == 401
    decisions = [d for d, _ in await _audit(svc_b, "bridge.receive")]
    assert decisions.count("allow") == 1 and decisions.count("deny") == 6


@pytest.mark.skipif(not uds.supported(), reason="needs AF_UNIX")
async def test_uds_addressing_needs_no_signature(services: Services, svc_b: Services, tmp_path: Path) -> None:
    team_b, _ = await _team_with_worker(svc_b)
    path = tmp_path / "b.sock"
    async with serving(create_app(svc_b.settings, services=svc_b), uds=path):
        ctx = await _sender(services, "http://127.0.0.1:9")
        out, is_error = await call_tool(
            services, ctx, "send_message", {"to": f"uds:{path}/worker", "message": "local"}
        )
    assert not is_error, out
    [mail] = await svc_b.ext["teams"].mail.messages(team_b, "worker")
    assert mail.sender == "uds:a/lead" and mail.text == "local"


def test_nonce_cache_window() -> None:
    cache = bridge.NonceCache(window_s=10)
    assert cache.add("n1", now=0) and not cache.add("n1", now=1)
    assert cache.add("n1", now=100)  # long expired → forgotten (the timestamp check covers it)
