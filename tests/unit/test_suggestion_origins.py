"""M7 W3 wave 2, phase A: suggestion origins and `m7.agents.strict_origins`.

A suggestion proposed from a connector chat, a job/webhook run or a kanban worker must never
run in that session on accept, and must never widen the agent it runs as (CUJ 1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from auoreo.core.db import AuditRow, DeliveryRow, KanbanTaskRow
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.suggestions import SuggestionError, suggestions_of
from auoreo.suggestions.origin import origin_of


async def delivery_session(svc: Services, kind: str, source: str) -> str:
    ses = await svc.create_session(title=f"{kind}:{source}")
    async with svc.db.session() as s:
        s.add(
            DeliveryRow(
                kind=kind, source=source, external_id=f"{kind}-1", payload={}, result={},
                status="delivering", session_id=ses.id,
            )
        )  # fmt: skip
        await s.commit()
    return ses.id


async def chat_session(svc: Services, agent: str = "default", key: str = "4242#ops") -> str:
    """A connector session, mapped under a routed `sessions.json` key (values are scanned)."""
    ses = await svc.create_session(title="telegram: ana", agent=agent)
    path = svc.settings.home / "plugins" / "telegram" / "sessions.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({key: ses.id}), encoding="utf-8")
    return ses.id


async def worker_session(svc: Services) -> str:
    ses = await svc.create_session(title="worker")
    async with svc.db.session() as s:
        s.add(KanbanTaskRow(board="triage", title="t", status="done", session_id=ses.id))
        await s.commit()
    return ses.id


def strict(svc: Services, on: bool = True) -> None:
    svc.settings.m7.agents.strict_origins = on


async def test_origin_kinds_and_sources(services: Services) -> None:
    job = await delivery_session(services, "job", "ops_monitor")
    hook = await delivery_session(services, "webhook", "issue-to-kanban")
    chat = await chat_session(services)
    worker = await worker_session(services)
    web = (await services.create_session(title="web", agent="ops")).id
    got = {sid: await origin_of(services, sid) for sid in (job, hook, chat, worker, web)}
    assert (got[job].kind, got[job].source) == ("cron", "job:ops_monitor")
    assert (got[hook].kind, got[hook].source) == ("cron", "webhook:issue-to-kanban")
    assert (got[chat].kind, got[chat].source) == ("platform", "plugin:telegram")
    assert (got[worker].kind, got[worker].source) == ("worker", "worker:triage")
    assert (got[web].kind, got[web].source, got[web].agent) == ("attended", "session", "ops")
    assert all(got[s].unattended for s in (job, hook, chat, worker)) and not got[web].unattended
    none = await origin_of(services, None)
    assert none.kind == "attended" and none.source == "session"


async def test_flag_off_keeps_todays_behaviour(services: Services) -> None:
    eng = suggestions_of(services)
    chat = await chat_session(services)
    row, _ = await eng.propose(
        "ops", "restart?", origin_session=chat, action={"type": "prompt", "text": "hi", "agent": "ops"}
    )
    assert row.action == {"type": "prompt", "text": "hi", "agent": "ops"}
    assert not await eng.is_unattended(chat)
    res = await eng.accept(row.id)
    assert res.session_id == chat  # today: the turn runs in the chat's own session
    await services.coordinator.wait(chat, 20)


async def test_strict_chat_suggestion_never_runs_in_the_chat_session(services: Services) -> None:
    """Adversarial (CUJ 1): a chat proposes `restart nginx` as agent `ops`; accept must open a
    new session as the chat's own agent, and the chat session itself can never accept."""
    strict(services)
    eng = suggestions_of(services)
    chat = await chat_session(services, agent="default")
    row, _ = await eng.propose(
        "ops", "restart nginx", origin_session=chat,
        action={"type": "prompt", "text": "restart nginx", "agent": "ops"},
    )  # fmt: skip
    assert row.action == {
        "type": "prompt", "text": "restart nginx", "origin_kind": "platform", "source": "plugin:telegram",
    }  # fmt: skip
    with pytest.raises(SuggestionError, match="unattended"):
        await eng.accept(row.id, session_id=chat)
    async with services.db.session() as s:
        q = select(AuditRow).where(AuditRow.action == "suggestion.accept", AuditRow.decision == "deny")
        assert len(list((await s.execute(q)).scalars())) == 1
    res = await eng.accept(row.id)
    assert res.session_id and res.session_id != chat
    new = await services.get_session(res.session_id)
    assert new is not None and new.agent == "default"  # never widened to `ops`
    await services.coordinator.wait(res.session_id, 20)
    assert services.coordinator.result(res.run_id or "").text == "echo: restart nginx"  # type: ignore[union-attr]

    def mentions(events: list) -> list:  # type: ignore[type-arg]
        return [e for e in events if "restart nginx" in json.dumps(e.data, default=str)]

    assert mentions(await services.events.replay(0, frozenset({f"session:{res.session_id}"})))
    assert not mentions(await services.events.replay(0, frozenset({f"session:{chat}"})))


async def test_strict_keeps_an_agent_equal_to_the_origin_agent(services: Services) -> None:
    strict(services)
    eng = suggestions_of(services)
    chat = await chat_session(services, agent="ops")
    row, _ = await eng.propose(
        "ops", "check disk", origin_session=chat, action={"type": "prompt", "text": "df", "agent": "ops"}
    )
    assert row.action["agent"] == "ops"
    res = await eng.accept(row.id)
    new = await services.get_session(res.session_id or "")
    assert new is not None and new.id != chat and new.agent == "ops"
    await services.coordinator.wait(new.id, 20)


@pytest.mark.parametrize("kind", ["job", "webhook", "worker"])
async def test_strict_other_unattended_origins_open_a_new_session(services: Services, kind: str) -> None:
    strict(services)
    eng = suggestions_of(services)
    sid = await (worker_session(services) if kind == "worker" else delivery_session(services, kind, "x"))
    row, _ = await eng.propose("ops", "go", origin_session=sid, action={"type": "prompt", "text": "go"})
    assert row.action["origin_kind"] == ("worker" if kind == "worker" else "cron")
    with pytest.raises(SuggestionError, match="unattended"):
        await eng.accept(row.id, session_id=sid)
    res = await eng.accept(row.id)
    assert res.session_id != sid
    await services.coordinator.wait(res.session_id or "", 20)


async def test_strict_attended_origin_still_runs_in_place(services: Services) -> None:
    strict(services)
    eng = suggestions_of(services)
    web = (await services.create_session(title="web")).id
    row, _ = await eng.propose("goal", "f", origin_session=web, action={"type": "prompt", "text": "x"})
    assert row.action["origin_kind"] == "attended" and row.action["source"] == "session"
    res = await eng.accept(row.id)
    assert res.session_id == web
    await services.coordinator.wait(web, 20)


async def test_stored_origin_kind_wins_when_the_mapping_is_gone(services: Services) -> None:
    """A chat mapping removed after propose (`/new`, file deleted) must not reopen the session."""
    strict(services)
    eng = suggestions_of(services)
    chat = await chat_session(services)
    row, _ = await eng.propose("ops", "x", origin_session=chat, action={"type": "prompt", "text": "x"})
    (services.settings.home / "plugins" / "telegram" / "sessions.json").unlink()
    res = await eng.accept(row.id)
    assert res.session_id != chat
    await services.coordinator.wait(res.session_id or "", 20)


async def test_strict_unattended_sessions_cannot_call_kanban_create(services: Services) -> None:
    """Adversarial: a webhook or chat agent must not queue dispatcher work directly; the only
    path is a `kanban_create` suggestion a human accepts."""
    from tests.teams_helpers import call_tool, tool_ctx

    hook = await delivery_session(services, "webhook", "issue-to-kanban")
    chat = await chat_session(services)
    worker = await worker_session(services)
    web = (await services.create_session(title="web")).id
    args = {"board": "triage", "title": "do it"}
    out, err = await call_tool(services, tool_ctx(services, hook), "kanban_create", args)
    assert not err, out  # flag off: today's behaviour
    strict(services)
    for sid in (hook, chat):
        out, err = await call_tool(services, tool_ctx(services, sid), "kanban_create", args)
        assert err and "suggest(kanban=" in out, out
    for sid in (worker, web):
        out, err = await call_tool(services, tool_ctx(services, sid), "kanban_create", args)
        assert not err, out
    async with services.db.session() as s:
        q = select(KanbanTaskRow).where(KanbanTaskRow.title == "do it")
        rows = list((await s.execute(q)).scalars())
    assert len(rows) == 3  # flag-off webhook + worker + web; nothing from the refused calls


@pytest.mark.parametrize("flag", ["kanban_suggestions", "post_consent", "voice_inbound"])
def test_wider_flags_refuse_to_turn_on_without_strict_origins(home: Path, flag: str) -> None:
    with pytest.raises(ValueError, match="strict_origins"):
        Settings(home=home, m7={"agents": {flag: True}})
    st = Settings(home=home, m7={"agents": {flag: True, "strict_origins": True}})
    assert getattr(st.m7.agents, flag) is True
