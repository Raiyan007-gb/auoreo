"""Broadcast rule (spec L1024): `send_message` to "*" needs a 1–10 word summary, is limited
to 5 per minute per team, and each broadcast writes a `mail.broadcast` audit row."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from auoreo.core.db import AuditRow
from auoreo.core.services import Services
from auoreo.teams import mailbox
from auoreo.teams.service import TeamsService
from tests.teams_helpers import RoutedGateway, call_tool, new_parent, tool_ctx


@pytest.fixture
def gateway() -> RoutedGateway:
    return RoutedGateway()


async def broadcast_audits(svc: Services) -> list[AuditRow]:
    async with svc.db.session() as s:
        return [r for r in (await s.execute(select(AuditRow))).scalars() if r.action == "mail.broadcast"]


async def team_with_two(svc: Services) -> tuple[TeamsService, str, str]:
    teams = svc.ext["teams"]
    lead = await new_parent(svc)
    a = await teams.spawn(lead, agent="default", prompt="CHILD-A", name="a", background=False)
    await teams.spawn(lead, agent="default", prompt="CHILD-B", name="b", background=False)
    return teams, lead, a.team_id


@pytest.mark.parametrize(
    ("summary", "ok"),
    [("", False), ("   ", False), ("one", True), (" ".join(["w"] * 10), True), (" ".join(["w"] * 11), False)],
)
async def test_broadcast_summary_must_have_one_to_ten_words(
    services: Services, summary: str, ok: bool
) -> None:
    _, lead, _ = await team_with_two(services)
    args = {"to": "*", "message": "status: see board:plan", "summary": summary}
    text, err = await call_tool(services, tool_ctx(services, lead), "send_message", args)
    if ok:
        assert not err and text == "delivered to a, b"
    else:
        assert err and "1–10 words" in text
    assert len(await broadcast_audits(services)) == (1 if ok else 0)


async def test_broadcast_is_rate_limited_per_team_and_audited(services: Services) -> None:
    teams, lead, team_id = await team_with_two(services)
    ctx = tool_ctx(services, lead)
    for i in range(mailbox.BROADCAST_PER_MIN):
        text, err = await call_tool(
            services, ctx, "send_message", {"to": "*", "message": f"m{i}", "summary": "ping"}
        )
        assert not err, text
    text, err = await call_tool(
        services, ctx, "send_message", {"to": "*", "message": "m6", "summary": "ping"}
    )
    assert err and "rate limit" in text
    # direct mail is not rate-limited
    text, err = await call_tool(services, ctx, "send_message", {"to": "a", "message": "direct"})
    assert not err and text == "delivered to a"

    audits = await broadcast_audits(services)
    assert len(audits) == mailbox.BROADCAST_PER_MIN
    assert audits[0].detail["team"] == team_id and audits[0].detail["recipients"] == ["a", "b"]
    assert audits[0].detail["summary"] == "ping" and audits[0].decision == "allow"

    # another team has its own window
    other = await new_parent(services)
    await teams.spawn(other, agent="default", prompt="CHILD-C", name="c", background=False)
    text, err = await call_tool(
        services, tool_ctx(services, other), "send_message", {"to": "*", "message": "hi", "summary": "hello"}
    )
    assert not err and text == "delivered to c"


async def test_broadcast_window_slides(services: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    teams, _, team_id = await team_with_two(services)
    now = [1000.0]
    monkeypatch.setattr(mailbox.time, "monotonic", lambda: now[0])
    for _ in range(mailbox.BROADCAST_PER_MIN):
        await teams.mail.send(team_id, "lead", "*", "x", "tick")
    with pytest.raises(mailbox.TeamsError, match="rate limit"):
        await teams.mail.send(team_id, "lead", "*", "x", "tick")
    now[0] += 61
    rows = await teams.mail.send(team_id, "lead", "*", "x", "tick")
    assert sorted(r.recipient for r in rows) == ["a", "b"]
