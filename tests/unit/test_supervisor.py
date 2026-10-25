"""Supervisor opt-in (W1-B, spec §16.3/L1024) and opt-in depth 3."""

from __future__ import annotations

import json

import pytest

from auoreo.core.services import Services
from auoreo.gateway.fake import Scripted
from auoreo.teams.format import SCHEMA_MISMATCH
from auoreo.teams.service import TeamsError, TeamsService
from tests.teams_helpers import RoutedGateway, call_tool, new_parent, tool_ctx, turn

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


@pytest.fixture
def gateway() -> RoutedGateway:
    return RoutedGateway()


def teams_of(svc: Services) -> TeamsService:
    teams = svc.ext["teams"]
    assert isinstance(teams, TeamsService)
    return teams


async def decision_events(svc: Services) -> list[dict]:  # type: ignore[type-arg]
    evs = await svc.events.replay(0, frozenset({"server"}), limit=100_000)
    return [e.data for e in evs if e.type == "team.decision"]


async def test_disabled_refuses_supervisor_role_and_logs_nothing(
    services: Services, gateway: RoutedGateway
) -> None:
    assert services.settings.teams_orchestrator_enabled is False
    teams = teams_of(services)
    coord = await new_parent(services, agent="coordinator")
    with pytest.raises(TeamsError, match="teams_orchestrator_enabled"):
        await teams.spawn(coord, agent="default", prompt="x", name="sup", background=True, role="supervisor")
    text, err = await call_tool(
        services,
        tool_ctx(services, coord),
        "delegate_task",
        {"goal": "x", "role": "supervisor", "name": "sup2"},
    )
    assert err and "opt-in" in text
    with pytest.raises(TeamsError, match="unknown role"):
        await teams.spawn(coord, agent="default", prompt="x", name="r", background=True, role="boss")
    # a coordinator without the opt-in spawns without output_schema and logs no decisions
    member = await teams.spawn(
        coord, agent="default", prompt="CHILD-PLAIN go", name="plain", background=False
    )
    assert member.status == "completed"
    assert await decision_events(services) == []


async def test_enabled_coordinator_needs_output_schema_and_logs_the_matrix(
    services: Services, gateway: RoutedGateway
) -> None:
    services.settings.teams_orchestrator_enabled = True
    teams = teams_of(services)
    coord = await new_parent(services, agent="coordinator")
    with pytest.raises(TeamsError, match="output_schema on every spawn"):
        await teams.spawn(coord, agent="default", prompt="x", name="noschema", background=True)

    gateway.route("CHILD-OK", Scripted(text=json.dumps({"answer": "42"})))
    ok = await teams.spawn(
        coord, agent="default", prompt="CHILD-OK go", name="ok", background=False, output_schema=SCHEMA
    )
    assert ok.status == "completed"
    gateway.route("CHILD-BAD", Scripted(text="not json"), Scripted(text="still not json"))
    bad = await teams.spawn(
        coord, agent="default", prompt="CHILD-BAD go", name="bad", background=False, output_schema=SCHEMA
    )
    assert bad.status == "failed"

    rows = await decision_events(services)
    assert [(d["child"], d["outcome"], d["decision"]) for d in rows] == [
        ("ok", "new", "spawn"),
        ("ok", "completed", "finish"),
        ("bad", "new", "spawn"),
        ("bad", SCHEMA_MISMATCH, "spawn"),
    ]
    assert all(d["team_id"] == ok.team_id and d["reason"] for d in rows)

    # mail from the supervisor to a finished child is a `continue` decision
    text, err = await call_tool(
        services, tool_ctx(services, coord), "send_message", {"to": "bad", "message": "narrow it to X"}
    )
    assert not err, text
    last = (await decision_events(services))[-1]
    assert (last["child"], last["decision"], last["outcome"]) == ("bad", "continue", "failed")

    listed = await teams.supervisor.decisions(ok.team_id)
    assert [d["decision"] for d in listed] == ["spawn", "finish", "spawn", "spawn", "continue"]
    assert listed[0]["seq"] < listed[-1]["seq"]


async def test_enabled_supervisor_role_child_must_pass_schema(
    services: Services, gateway: RoutedGateway
) -> None:
    services.settings.teams_orchestrator_enabled = True
    teams = teams_of(services)
    lead = await new_parent(services)  # a plain default agent is not a supervisor ...
    gateway.route("CHILD-SUP", Scripted(text="supervising"))
    sup = await teams.spawn(
        lead, agent="default", prompt="CHILD-SUP go", name="sup", background=False, role="supervisor"
    )
    assert sup.status == "completed" and await decision_events(services) == []
    # ... but a child spawned with role=supervisor is
    assert teams.supervisor.is_supervisor(sup.session_id, "default")
    with pytest.raises(TeamsError, match="output_schema"):
        await teams.spawn(sup.session_id, agent="default", prompt="x", name="w1", background=True)
    gateway.route("CHILD-W", Scripted(text=json.dumps({"answer": "w"})))
    worker = await teams.spawn(
        sup.session_id,
        agent="default",
        prompt="CHILD-W go",
        name="w2",
        background=False,
        output_schema=SCHEMA,
    )
    assert worker.depth == 2 and worker.team_id == sup.team_id
    assert [d["decision"] for d in await decision_events(services)] == ["spawn", "finish"]
    with pytest.raises(TeamsError, match="unknown decision"):
        await teams.supervisor.decide(sup.team_id, "w2", "completed", "retry", "x")


async def test_depth_three_is_opt_in(services: Services, gateway: RoutedGateway) -> None:
    teams = teams_of(services)
    s0 = await new_parent(services)
    s1 = (await services.create_session(title="s1", parent_id=s0)).id
    s2 = (await services.create_session(title="s2", parent_id=s1)).id
    with pytest.raises(TeamsError, match="teams_max_depth=2"):
        await teams.spawn(s2, agent="default", prompt="x", name="deep", background=True)

    services.settings.teams_max_depth = 3
    gateway.route("CHILD-DEEP", Scripted(text="from depth three"))
    deep = await teams.spawn(s2, agent="default", prompt="CHILD-DEEP go", name="deep", background=False)
    assert deep.depth == 3 and deep.status == "completed" and deep.text == "from depth three"
    # the depth-3 child is a leaf: it may not delegate further
    text, err = await call_tool(services, tool_ctx(services, deep.session_id), "delegate_task", {"goal": "x"})
    assert err and "teams_max_depth=3" in text


async def test_delegate_task_tool_logs_spawn_decision(services: Services, gateway: RoutedGateway) -> None:
    services.settings.teams_orchestrator_enabled = True
    args = {"goal": "CHILD-TOOL do it", "name": "tool_kid", "output_schema": SCHEMA}
    gateway.route("PARENT-SUP", Scripted(tool_calls=[("delegate_task", args)]), Scripted(text="integrated"))
    gateway.route("CHILD-TOOL", Scripted(text=json.dumps({"answer": "done"})))
    coord = await new_parent(services, agent="coordinator")
    result = await turn(services, coord, "PARENT-SUP go")
    assert result.status == "completed"
    assert [(d["child"], d["decision"]) for d in await decision_events(services)] == [
        ("tool_kid", "spawn"),
        ("tool_kid", "finish"),
    ]
