"""Goals: tools through the fake gateway, absorb merge + ref rewrite, events."""

from __future__ import annotations

import pytest

from auoreo.core.services import Services
from auoreo.goals import GoalError, GoalsService, goals_of
from tests.teams_helpers import call_tool, new_parent, tool_ctx, turn


async def server_events(svc: Services, prefix: str) -> list[tuple[str, dict]]:  # type: ignore[type-arg]
    return [
        (e.type, e.data)
        for e in await svc.events.replay(0, frozenset({"server"}))
        if e.type.startswith(prefix)
    ]


def test_goals_extension_started(services: Services) -> None:
    assert isinstance(services.ext["goals"], GoalsService)
    assert goals_of(services) is services.ext["goals"]


def test_goal_tools_are_deferred_with_goals_permission(services: Services) -> None:
    tools = services.harness.tools
    for name in ("get_goal", "create_goal", "update_goal"):
        m = tools[name].manifest
        assert m.exposure == "deferred" and m.permission_name == "goals" and m.default_action == "allow"
    assert tools["get_goal"].manifest.is_read_only
    assert not tools["create_goal"].manifest.is_read_only and not tools["update_goal"].manifest.is_read_only


async def test_goal_tools_through_the_fake_gateway(services: Services) -> None:
    sid = await new_parent(services)
    res = await turn(services, sid, '/tool tool_search {"query": "goal"}')
    assert res.status == "completed"
    res = await turn(
        services, sid, '/tool create_goal {"title": "ship M6", "description": "all gates green"}'
    )
    assert res.status == "completed" and "created goal #1" in res.text
    row = await goals_of(services).require(1)
    assert row.title == "ship M6" and row.status == "active" and row.session_id == sid
    assert row.evidence_session_ids == [sid]

    other = await new_parent(services, title="other")
    await turn(services, other, '/tool tool_search {"query": "goal"}')
    res = await turn(services, other, '/tool update_goal {"id": 1, "progress": 0.5}')
    assert "50%" in res.text
    row = await goals_of(services).require(1)
    assert row.progress == 0.5 and row.evidence_session_ids == [sid, other]

    res = await turn(services, other, '/tool get_goal {"id": 1}')
    assert "ship M6" in res.text and other in res.text
    created = await server_events(services, "goal.")
    assert [t for t, _ in created] == ["goal.created", "goal.updated"]
    assert created[0][1]["title"] == "ship M6" and created[1][1]["progress"] == 0.5


async def test_get_goal_lists_and_filters(services: Services) -> None:
    sid = await new_parent(services)
    ctx = tool_ctx(services, sid)
    await call_tool(services, ctx, "create_goal", {"title": "a"})
    await call_tool(services, ctx, "create_goal", {"title": "b"})
    out, err = await call_tool(services, ctx, "update_goal", {"id": 2, "status": "done"})
    assert not err and "[done]" in out
    out, _ = await call_tool(services, ctx, "get_goal", {})
    assert "#1" in out and "#2" not in out
    out, _ = await call_tool(services, ctx, "get_goal", {"status": "done"})
    assert "#2" in out and "#1" not in out
    out, _ = await call_tool(services, ctx, "get_goal", {"status": "all"})
    assert "#1" in out and "#2" in out
    out, err = await call_tool(services, ctx, "get_goal", {"id": 99})
    assert err and "not found" in out
    out, err = await call_tool(services, ctx, "update_goal", {"id": 1})
    assert err and "nothing to update" in out
    out, err = await call_tool(services, ctx, "update_goal", {"id": 1, "status": "absorbed"})
    assert err  # schema enum: absorbing goes through absorb_into


async def test_absorb_merges_evidence_and_rewrites_refs(services: Services) -> None:
    g = goals_of(services)
    a = await g.create("A", session_id="ses_a")
    b = await g.create("B", session_id="ses_b")
    c = await g.create("C", session_id="ses_c")
    await g.update(a.id, evidence_session="ses_shared")
    await g.update(b.id, evidence_session="ses_shared")
    target = await g.absorb(a.id, b.id)
    assert target.id == b.id and target.evidence_session_ids == ["ses_b", "ses_shared", "ses_a"]
    src = await g.require(a.id)
    assert src.status == "absorbed" and src.absorbed_into == b.id
    # B absorbed into C: A (absorbed into B) is re-pointed at C
    await g.absorb(b.id, c.id)
    assert (await g.require(a.id)).absorbed_into == c.id
    assert (await g.require(c.id)).evidence_session_ids == ["ses_c", "ses_b", "ses_shared", "ses_a"]
    with pytest.raises(GoalError, match="already absorbed"):
        await g.absorb(a.id, c.id)
    with pytest.raises(GoalError, match="absorb into that one"):
        await g.absorb(c.id, b.id)
    with pytest.raises(GoalError, match="itself"):
        await g.absorb(c.id, c.id)
    with pytest.raises(GoalError, match="update that one"):
        await g.update(a.id, status="active")
    absorbed = [d for t, d in await server_events(services, "goal.updated") if d["status"] == "absorbed"]
    assert {d["id"] for d in absorbed} == {a.id, b.id}


async def test_update_goal_tool_absorb_into(services: Services) -> None:
    sid = await new_parent(services)
    ctx = tool_ctx(services, sid)
    await call_tool(services, ctx, "create_goal", {"title": "dup one"})
    await call_tool(services, ctx, "create_goal", {"title": "dup two"})
    out, err = await call_tool(services, ctx, "update_goal", {"id": 1, "absorb_into": 2})
    assert not err and "absorbed into #2" in out
    assert (await goals_of(services).require(1)).status == "absorbed"


async def test_update_validation(services: Services) -> None:
    g = goals_of(services)
    row = await g.create("x")
    with pytest.raises(GoalError, match="between 0 and 1"):
        await g.update(row.id, progress=1.5)
    with pytest.raises(GoalError, match="not found"):
        await g.update(404, progress=0.1)
    with pytest.raises(GoalError, match="empty"):
        await g.create("   ")
    with pytest.raises(GoalError, match="unknown status"):
        await g.list("bogus")


def test_goal_command_is_a_prompt_command(services: Services) -> None:
    cmd = services.harness.commands["goal"]
    assert cmd.manifest.handler == "prompt" and "create_goal" in cmd.template
