from __future__ import annotations

import asyncio
from datetime import timedelta

from auoreo.core.db import KanbanTaskRow, utcnow
from auoreo.core.services import Services
from auoreo.teams.kanban import KanbanStore
from tests.teams_helpers import call_tool, new_parent, tool_ctx


def store(svc: Services) -> KanbanStore:
    kb = svc.ext["teams"].kanban
    assert isinstance(kb, KanbanStore)
    return kb


async def server_events(svc: Services) -> list[tuple[str, dict]]:  # type: ignore[type-arg]
    return [(e.type, e.data) for e in await svc.events.replay(0, frozenset({"server"}))]


async def test_atomic_claim_two_concurrent_claimers_one_winner(services: Services) -> None:
    kb = store(services)
    task = await kb.create("b1", "only task")
    results = await asyncio.gather(kb.claim("b1", "alice"), kb.claim("b1", "bob"))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1 and winners[0].id == task.id and winners[0].status == "claimed"
    assert winners[0].worker in {"alice", "bob"} and winners[0].heartbeat_at is not None
    assert await kb.claim("b1", "carol") is None
    claimed = [d for t, d in await server_events(services) if t == "kanban.task_claimed"]
    assert claimed == [{"id": task.id, "board": "b1", "worker": winners[0].worker}]


async def test_many_claimers_each_get_a_distinct_task(services: Services) -> None:
    kb = store(services)
    for i in range(5):
        await kb.create("b2", f"t{i}")
    await kb.create("other", "not on this board")
    got = await asyncio.gather(*(kb.claim("b2", f"w{i}") for i in range(8)))
    ids = [r.id for r in got if r is not None]
    assert len(ids) == 5 and len(set(ids)) == 5


async def test_stale_claim_is_reclaimed(services: Services) -> None:
    kb = store(services)
    task = await kb.create("b3", "stalls")
    await kb.claim("b3", "w1")
    assert await kb.reclaim_stale() == []  # fresh heartbeat
    async with services.db.session() as s:
        row = await s.get(KanbanTaskRow, task.id)
        assert row is not None
        row.heartbeat_at = utcnow() - timedelta(seconds=400)
        await s.commit()
    assert await kb.reclaim_stale() == [task.id]
    row = await kb.get(task.id)
    assert row.status == "ready" and row.worker is None and row.failures == 1
    again = await kb.claim("b3", "w2")
    assert again is not None and again.worker == "w2"
    hb = await kb.heartbeat(task.id, "w2")
    assert hb.heartbeat_at is not None and hb.heartbeat_at >= again.heartbeat_at  # type: ignore[operator]


async def test_auto_block_after_two_failures(services: Services) -> None:
    kb = store(services)
    task = await kb.create("b4", "flaky")
    await kb.claim("b4", "w1")
    first = await kb.block(task.id, "tests fail", "w1")
    assert first.status == "ready" and first.failures == 1
    await kb.claim("b4", "w2")
    second = await kb.block(task.id, "still failing", "w2")
    assert second.status == "blocked" and second.failures == 2
    assert await kb.claim("b4", "w3") is None
    blocked = [d for t, d in await server_events(services) if t == "kanban.blocked"]
    assert blocked == [{"id": task.id, "board": "b4", "worker": "w2"}]
    assert [c.text for c in await kb.comments(task.id)] == ["blocked: tests fail", "blocked: still failing"]


async def test_kanban_tools_round_trip(services: Services) -> None:
    sid = await new_parent(services)
    ctx = tool_ctx(services, sid)
    text, err = await call_tool(services, ctx, "kanban_create", {"board": "web", "title": "fix header"})
    assert not err and text.startswith("created #")
    text, err = await call_tool(services, ctx, "kanban_claim", {"board": "web", "worker": "me"})
    assert not err and "claimed #" in text and "worker=me" in text
    task_id = int(text.split("#")[1].split()[0])
    text, err = await call_tool(services, ctx, "kanban_heartbeat", {"task_id": task_id, "worker": "me"})
    assert not err
    text, err = await call_tool(services, ctx, "kanban_complete", {"task_id": task_id, "worker": "other"})
    assert err and "claimed by me" in text
    text, err = await call_tool(services, ctx, "kanban_comment", {"task_id": task_id, "text": "halfway"})
    assert not err
    text, err = await call_tool(
        services, ctx, "kanban_complete", {"task_id": task_id, "worker": "me", "summary": "done it"}
    )
    assert not err and "[done]" in text
    text, err = await call_tool(services, ctx, "kanban_list", {"board": "web", "status": "done"})
    assert not err and "fix header" in text
    text, err = await call_tool(services, ctx, "kanban_claim", {"board": "web"})
    assert not err and text == "no ready task on this board"
    text, err = await call_tool(services, ctx, "kanban_block", {"task_id": 999, "reason": "x"})
    assert err and "unknown kanban task 999" in text
    assert any(t == "kanban.done" for t, _ in await server_events(services))
