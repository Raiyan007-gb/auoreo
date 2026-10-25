"""Suggestion engine: MAX_PENDING, dedupe, consent (accept/deny), expiry sweep, outbox
delivery with fallback to home, quiet hours, and the unattended-accept refusal."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from auoreo.backends import delivery as providers
from auoreo.backends.delivery import DeliveryProvider, DeliveryReceipt
from auoreo.core.db import AuditRow, DeliveryRow, SuggestionRow, utcnow
from auoreo.core.services import Services
from auoreo.suggestions import TOMBSTONES, SuggestionEngine, SuggestionError, suggestions_of
from auoreo.suggestions.delivery import in_quiet, render
from tests.teams_helpers import call_tool, new_parent, tool_ctx


class Failing(DeliveryProvider):
    def __init__(self) -> None:
        super().__init__()
        self.sent: list[tuple[str, str]] = []

    async def send(self, target: str, text: str, *, meta: dict[str, Any]) -> DeliveryReceipt:
        self.sent.append((target, text))
        return DeliveryReceipt(False, "boom")


@pytest.fixture
def failing() -> Iterator[tuple[Failing, Failing]]:
    a, b = Failing(), Failing()
    providers.register("failing", a)
    providers.register("failing2", b)
    yield a, b
    providers.unregister("failing")
    providers.unregister("failing2")


def engine(svc: Services) -> SuggestionEngine:
    eng = svc.ext["suggestions"]
    assert isinstance(eng, SuggestionEngine) and suggestions_of(svc) is eng
    return eng


async def server_events(svc: Services, prefix: str) -> list[tuple[str, dict]]:  # type: ignore[type-arg]
    return [
        (e.type, e.data)
        for e in await svc.events.replay(0, frozenset({"server"}))
        if e.type.startswith(prefix)
    ]


async def suggestion_deliveries(svc: Services) -> list[DeliveryRow]:
    async with svc.db.session() as s:
        q = select(DeliveryRow).where(DeliveryRow.kind == "suggestion").order_by(DeliveryRow.id)
        return list((await s.execute(q)).scalars())


async def test_max_pending_expires_the_oldest(services: Services) -> None:
    eng = engine(services)
    ids = [(await eng.propose("info", f"s{i}"))[0].id for i in range(7)]
    pending = await eng.list("pending")
    assert sorted(r.id for r in pending) == ids[2:]
    assert sorted(r.id for r in await eng.list("expired")) == ids[:2]
    expired = [d for t, d in await server_events(services, "suggestion.expired")]
    assert [d["id"] for d in expired] == ids[:2] and {d["reason"] for d in expired} == {"max_pending"}
    assert len([1 for t, _ in await server_events(services, "suggestion.proposed")]) == 7
    with pytest.raises(SuggestionError, match="not pending"):
        await eng.accept(ids[0])


async def test_dedupe_while_pending(services: Services) -> None:
    eng = engine(services)
    first, created = await eng.propose("ops", "disk full", dedupe_key="ops:disk:/")
    again, created2 = await eng.propose("ops", "disk still full", dedupe_key="ops:disk:/")
    assert created and not created2 and again.id == first.id
    await eng.deny(first.id)
    third, created3 = await eng.propose("ops", "disk full again", dedupe_key="ops:disk:/")
    assert created3 and third.id != first.id


async def test_accept_submits_a_turn_in_the_origin_session(services: Services) -> None:
    eng = engine(services)
    sid = await new_parent(services)
    row, _ = await eng.propose(
        "goal", "follow up", origin_session=sid, action={"type": "prompt", "text": "hello from a suggestion"}
    )
    res = await eng.accept(row.id)
    assert res.session_id == sid and res.run_id and res.row.status == "accepted"
    await services.coordinator.wait(sid, 20)
    result = services.coordinator.result(res.run_id)
    assert result is not None and result.text == "echo: hello from a suggestion"
    accepted = [d for _, d in await server_events(services, "suggestion.accepted")]
    assert accepted == [accepted[0]] and accepted[0]["session_id"] == sid
    with pytest.raises(SuggestionError, match="accepted, not pending"):
        await eng.accept(row.id)
    async with services.db.session() as s:
        audit = (await s.execute(select(AuditRow).where(AuditRow.action == "suggestion.accept"))).scalars()
        assert [a.decision for a in audit] == ["allow"]


async def test_accept_without_origin_opens_a_new_session(services: Services) -> None:
    eng = engine(services)
    row, _ = await eng.propose("info", "go", action={"type": "prompt", "text": "new session please"})
    res = await eng.accept(row.id)
    assert res.session_id and res.session_id.startswith("ses_")
    await services.coordinator.wait(res.session_id, 20)
    assert services.coordinator.result(res.run_id or "").text == "echo: new session please"  # type: ignore[union-attr]


async def test_accept_without_action_only_records_consent(services: Services) -> None:
    eng = engine(services)
    row, _ = await eng.propose("info", "fyi")
    res = await eng.accept(row.id)
    assert res.session_id is None and res.run_id is None and res.row.status == "accepted"


async def test_deny_tombstones(services: Services) -> None:
    eng = engine(services)
    row, _ = await eng.propose("info", "nah", action={"type": "prompt", "text": "never runs"})
    denied = await eng.deny(row.id)
    assert denied.status == "denied" and denied.status in TOMBSTONES
    with pytest.raises(SuggestionError, match="denied"):
        await eng.accept(row.id)
    with pytest.raises(SuggestionError, match="not found"):
        await eng.deny(999)
    assert [t for t, _ in await server_events(services, "suggestion.denied")] == ["suggestion.denied"]


async def test_expiry_sweep_tombstones_expired(services: Services) -> None:
    eng = engine(services)
    old, _ = await eng.propose("info", "stale")
    fresh, _ = await eng.propose("info", "fresh")
    async with services.db.session() as s:
        r = await s.get(SuggestionRow, old.id)
        assert r is not None
        r.expires_at = utcnow() - timedelta(seconds=1)
        await s.commit()
    assert (await eng.sweep())["expired"] == 1
    assert (await eng.get(old.id)).status == "expired"  # type: ignore[union-attr]
    assert (await eng.get(fresh.id)).status == "pending"  # type: ignore[union-attr]
    with pytest.raises(SuggestionError, match="expired"):
        await eng.accept(old.id)
    # past its TTL but not yet swept: accept refuses and expires it
    async with services.db.session() as s:
        r = await s.get(SuggestionRow, fresh.id)
        assert r is not None
        r.expires_at = utcnow() - timedelta(seconds=1)
        await s.commit()
    with pytest.raises(SuggestionError, match="has expired"):
        await eng.accept(fresh.id)
    assert (await eng.get(fresh.id)).status == "expired"  # type: ignore[union-attr]


async def test_origin_suggestions_are_not_delivered(services: Services) -> None:
    eng = engine(services)
    await eng.propose("info", "ui only")
    await eng.drain()
    assert await suggestion_deliveries(services) == []


async def test_delivery_to_home_goes_through_the_outbox(services: Services) -> None:
    eng = engine(services)
    row, _ = await eng.propose("ops", "cpu hot", "CPU at 97% for 10 min", deliver="home")
    await eng.drain()
    [d] = await suggestion_deliveries(services)
    assert d.status == "delivered" and d.target == "log" and d.result["provider"] == "log"
    assert d.external_id == f"suggestion-{row.id}" and d.attempts == 1
    assert (await eng.get(row.id)).delivery_id == d.id  # type: ignore[union-attr]
    log_text = (services.settings.home / "deliveries.log").read_text(encoding="utf-8")
    assert "cpu hot" in log_text and "CPU at 97%" in log_text
    assert (await eng.get(row.id)).status == "pending"  # delivered ≠ accepted  # type: ignore[union-attr]
    types = [t for t, _ in await server_events(services, "delivery.")]
    assert types[:1] == ["delivery.queued"] and "delivery.delivered" in types
    assert await eng.deliver(row.id) == "skipped"  # one delivery per suggestion


async def test_failed_delivery_falls_back_to_home(
    services: Services, failing: tuple[Failing, Failing]
) -> None:
    eng = engine(services)
    row, _ = await eng.propose("ops", "mem hot", deliver="failing:chat-1")
    await eng.drain()
    first, second = await suggestion_deliveries(services)
    assert first.status == "failed" and first.target == "failing:chat-1" and "boom" in (first.error or "")
    assert second.status == "delivered" and second.target == "log"
    assert second.external_id == f"suggestion-{row.id}-home"
    assert failing[0].sent and failing[0].sent[0][0] == "chat-1"
    after = await eng.get(row.id)
    assert after is not None and after.status == "pending" and after.delivery_id == second.id


async def test_second_failure_tombstones(services: Services, failing: tuple[Failing, Failing]) -> None:
    services.settings.suggestions_home = "failing2"
    eng = engine(services)
    row, _ = await eng.propose("ops", "disk hot", deliver="failing")
    await eng.drain()
    rows = await suggestion_deliveries(services)
    assert [r.status for r in rows] == ["failed", "failed"]
    assert len(failing[0].sent) == 1 and len(failing[1].sent) == 1  # one attempt each
    assert (await eng.get(row.id)).status == "tombstoned"  # type: ignore[union-attr]
    failed = [d for _, d in await server_events(services, "suggestion.failed")]
    assert [d["id"] for d in failed] == [row.id]
    with pytest.raises(SuggestionError, match="tombstoned"):
        await eng.accept(row.id)


async def test_home_failure_is_one_attempt(services: Services, failing: tuple[Failing, Failing]) -> None:
    services.settings.suggestions_home = "failing"
    eng = engine(services)
    row, _ = await eng.propose("ops", "x", deliver="home")
    await eng.drain()
    assert len(await suggestion_deliveries(services)) == 1 and len(failing[0].sent) == 1
    assert (await eng.get(row.id)).status == "tombstoned"  # type: ignore[union-attr]


async def test_quiet_hours_defer_delivery_not_proposal(services: Services) -> None:
    services.settings.suggestions_quiet_hours = "22:00-07:00"
    eng = engine(services)
    eng.local_now = lambda: datetime(2026, 10, 25, 23, 30)
    row, created = await eng.propose("ops", "quiet please", deliver="home")
    await eng.drain()
    assert created and (await eng.get(row.id)).status == "pending"  # type: ignore[union-attr]
    assert await suggestion_deliveries(services) == []
    assert await eng.deliver(row.id) == "deferred"
    assert (await eng.sweep())["delivered"] == 0
    eng.local_now = lambda: datetime(2026, 10, 26, 7, 0)
    assert (await eng.sweep())["delivered"] == 1
    [d] = await suggestion_deliveries(services)
    assert d.status == "delivered"


def test_quiet_window_math() -> None:
    at = lambda h, m=0: datetime(2026, 1, 1, h, m)  # noqa: E731
    assert in_quiet("22:00-07:00", at(23)) and in_quiet("22:00-07:00", at(3))
    assert not in_quiet("22:00-07:00", at(7)) and not in_quiet("22:00-07:00", at(12))
    assert in_quiet("9:00-17:00", at(9)) and not in_quiet("9:00-17:00", at(17))
    assert not in_quiet(None, at(23)) and not in_quiet("08:00-08:00", at(8))


async def test_unattended_sessions_cannot_accept(services: Services) -> None:
    eng = engine(services)
    job_ses = await services.create_session(title="job:ops_monitor")
    async with services.db.session() as s:
        s.add(
            DeliveryRow(
                kind="job", source="ops_monitor", external_id="run-1", payload={}, result={},
                status="delivering", session_id=job_ses.id,
            )
        )  # fmt: skip
        await s.commit()
    row, _ = await eng.propose(
        "ops", "restart?", origin_session=job_ses.id, action={"type": "prompt", "text": "x"}
    )
    with pytest.raises(SuggestionError, match="unattended"):
        await eng.accept(row.id, session_id=job_ses.id)
    with pytest.raises(SuggestionError, match="unattended"):
        await eng.accept(row.id, interactive=False)
    assert (await eng.get(row.id)).status == "pending"  # type: ignore[union-attr]
    # a human accept runs the prompt in a NEW session, not the headless job session
    res = await eng.accept(row.id)
    assert res.session_id != job_ses.id
    await services.coordinator.wait(res.session_id or "", 20)


async def test_suggest_tool_proposes_and_dedupes(services: Services) -> None:
    sid = await new_parent(services)
    ctx = tool_ctx(services, sid)
    args = {"title": "disk / at 93%", "kind": "ops", "dedupe_key": "ops:disk:/", "prompt": "free space"}
    out, err = await call_tool(services, ctx, "suggest", args)
    assert not err and "proposed suggestion #1" in out and "consent" in out
    out, err = await call_tool(services, ctx, "suggest", args)
    assert not err and "already pending as suggestion #1" in out
    row = await engine(services).get(1)
    assert (
        row is not None
        and row.origin_session == sid
        and row.action == {"type": "prompt", "text": "free space"}
    )
    out, err = await call_tool(services, ctx, "suggest", {"title": "x", "deliver": "nowhere"})
    assert err and "unknown delivery target" in out
    m = services.harness.tools["suggest"].manifest
    assert m.exposure == "deferred" and m.permission_name == "suggest" and m.default_action == "allow"


async def test_disabled_engine_refuses(services: Services) -> None:
    services.settings.suggestions_enabled = False
    with pytest.raises(SuggestionError, match="disabled"):
        await engine(services).propose("info", "x")


def test_render_caps_at_4000_chars() -> None:
    row = SuggestionRow(id=7, kind="info", title="t", body="x" * 9000)
    text = render(row)
    assert len(text) <= 4000 and text.startswith("[suggestion #7] t") and "accept or deny" in text


def test_ops_monitor_job_uses_suggest(services: Services) -> None:
    job = services.harness.jobs["ops_monitor"]
    assert job.manifest.enabled is False
    prompt = job.manifest.prompt
    assert "suggest" in prompt and "dedupe_key" in prompt and "90" in prompt
    agent = services.harness.agent("ops")
    assert "suggest" in agent.manifest.tools.allow
