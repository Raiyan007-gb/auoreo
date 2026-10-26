from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from auoreo.core.services import Services
from auoreo.gateway.fake import Scripted
from auoreo.gateway.types import text_of
from auoreo.teams.ceiling import CeilingEngine, clamp
from auoreo.teams.service import TeamsError, TeamsService
from tests.teams_helpers import RoutedGateway, call_tool, new_parent, tool_ctx, turn, write_agent

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


async def events_of(svc: Services, sid: str) -> list[tuple[str, dict]]:  # type: ignore[type-arg]
    evs = await svc.events.replay(0, frozenset({f"session:{sid}"}), limit=100_000)
    return [(e.type, e.data) for e in evs]


async def test_foreground_delegate_runs_child_and_returns_task_notification(
    services: Services, gateway: RoutedGateway
) -> None:
    args = {"goal": "CHILD-FG summarize the repo", "agent": "planner", "name": "kid"}
    gateway.route("PARENT-FG", Scripted(tool_calls=[("delegate_task", args)]), Scripted(text="parent done"))
    gateway.route("CHILD-FG", Scripted(text="child says hi"))
    parent = await new_parent(services)
    result = await turn(services, parent, "PARENT-FG go")
    assert result.status == "completed" and result.text == "parent done"

    # the parent's second request carries the tool result: one task notification
    tool_msgs = [m for m in gateway.requests_for("PARENT-FG")[-1].messages if m.get("role") == "tool"]
    note = text_of(tool_msgs[-1]["content"])
    assert note.startswith("<task-notification>") and "<status>completed</status>" in note
    assert "<result>child says hi</result>" in note and "<usage>prompt_tokens=" in note

    child_rows = [s for s in await services.list_sessions() if s.parent_id == parent]
    assert len(child_rows) == 1 and child_rows[0].agent == "planner" and child_rows[0].title == "kid"
    assert f"<task-id>{child_rows[0].id}</task-id>" in note
    kinds = [t for t, _ in await events_of(services, parent) if t.startswith("subagent.")]
    assert kinds == ["subagent.spawn_requested", "subagent.start", "subagent.complete"]

    team = await teams_of(services).team_of(parent)
    assert team is not None and team.lead_session_id == parent and team.name == team.id
    members = await teams_of(services).members(team.id)
    assert [(m.name, m.agent, m.depth, m.status) for m in members] == [("kid", "planner", 1, "done")]


async def test_background_delegate_injects_notification_into_parent_next_request(
    services: Services, gateway: RoutedGateway
) -> None:
    args = {"goal": "CHILD-BG compute", "name": "bg", "background": True}
    gateway.route(
        "PARENT-BG",
        Scripted(tool_calls=[("delegate_task", args)]),
        Scripted(text="started it"),
        Scripted(text="got the result"),
    )
    gateway.route("CHILD-BG", Scripted(text="background result 42"))
    parent = await new_parent(services)
    result = await turn(services, parent, "PARENT-BG go")
    assert result.text == "started it"
    first_tool = [m for m in gateway.requests_for("PARENT-BG")[1].messages if m.get("role") == "tool"][-1]
    assert "Started background task 'bg'" in text_of(first_tool["content"])

    teams = teams_of(services)
    team = await teams.team_of(parent)
    assert team is not None
    done = await teams.wait(team.id, "bg", 10_000)
    assert done["status"] == "completed" and done["result"] == "background result 42"

    await turn(services, parent, "anything new?")
    last = gateway.requests_for("PARENT-BG")[-1]
    injected = [text_of(m["content"]) for m in last.messages if m.get("role") == "user"]
    assert any("<task-notification>" in t and "background result 42" in t for t in injected)


async def test_depth_limit_refuses_at_max_depth(services: Services) -> None:
    teams = teams_of(services)
    s0 = await new_parent(services)
    s1 = (await services.create_session(title="s1", parent_id=s0)).id
    s2 = (await services.create_session(title="s2", parent_id=s1)).id
    assert await teams.depth(s2) == services.settings.teams_max_depth == 2
    with pytest.raises(TeamsError, match="depth limit"):
        await teams.spawn(s2, agent="default", prompt="x", name="too_deep", background=True)
    text, err = await call_tool(services, tool_ctx(services, s2), "delegate_task", {"goal": "x"})
    assert err and "leaf agents" in text
    with pytest.raises(TeamsError, match="invalid task name"):
        await teams.spawn(s0, agent="default", prompt="x", name="Bad-Name", background=True)
    with pytest.raises(TeamsError, match="unknown agent"):
        await teams.spawn(s0, agent="nope", prompt="x", name="ok", background=True)


async def test_concurrency_cap_queues_the_fourth_child(services: Services, gateway: RoutedGateway) -> None:
    teams = teams_of(services)
    gate = asyncio.Event()
    parent = await new_parent(services)
    members = []
    for i in range(4):
        gateway.route(f"SLOW-{i}", Scripted(text=f"slow {i} done"), gate=gate)
        members.append(
            await teams.spawn(parent, agent="default", prompt=f"SLOW-{i}", name=f"w{i}", background=True)
        )
    await asyncio.sleep(0.2)
    assert [m.status for m in members] == ["running", "running", "running", "queued"]
    assert teams.fleet.running[parent] == 3
    queued = [d for t, d in await events_of(services, parent) if t == "subagent.queued"]
    assert queued == [
        {
            "parent": parent,
            "child": members[3].session_id,
            "name": "w3",
            "agent": "default",
            "position": 1,
        }
    ]
    gate.set()
    team = await teams.team_of(parent)
    assert team is not None
    for m in members:
        await teams.wait(team.id, m.name, 10_000)
    assert [m.status for m in members] == ["completed"] * 4
    assert [m.text for m in members] == [f"slow {i} done" for i in range(4)]
    assert teams.fleet.running[parent] == 0 and teams.fleet.total == 0


async def test_output_schema_mismatch_retries_once_then_succeeds(
    services: Services, gateway: RoutedGateway
) -> None:
    gateway.route("SCHEMA-OK", Scripted(text="sure, the answer is yes"), Scripted(text='{"answer": "yes"}'))
    parent = await new_parent(services)
    m = await teams_of(services).spawn(
        parent, agent="default", prompt="SCHEMA-OK q", name="s", background=False, output_schema=SCHEMA
    )
    assert m.status == "completed" and m.text == '{"answer": "yes"}'
    reqs = gateway.requests_for("SCHEMA-OK")
    assert len(reqs) == 2
    retry = text_of(reqs[1].messages[-1]["content"])
    assert "TOOL_OUTPUT_SCHEMA_MISMATCH" in retry and '"answer"' in retry
    assert "JSON Schema" in text_of(reqs[0].messages[-1]["content"])


async def test_output_schema_mismatch_twice_is_an_error_result(
    services: Services, gateway: RoutedGateway
) -> None:
    gateway.route("SCHEMA-BAD", Scripted(text="nope"), Scripted(text='{"answer": 3}'))
    parent = await new_parent(services)
    args = {"goal": "SCHEMA-BAD q", "name": "bad", "output_schema": SCHEMA}
    text, err = await call_tool(services, tool_ctx(services, parent), "delegate_task", args)
    assert err and "[TOOL_OUTPUT_SCHEMA_MISMATCH]" in text and "<status>failed</status>" in text
    assert len(gateway.requests_for("SCHEMA-BAD")) == 2  # exactly one retry


async def test_mailbox_send_reaches_recipient_next_request(
    services: Services, gateway: RoutedGateway
) -> None:
    teams = teams_of(services)
    gateway.route("CHILD-MAIL", Scripted(text="ready"), Scripted(text="read your mail"))
    parent = await new_parent(services)
    kid = await teams.spawn(parent, agent="default", prompt="CHILD-MAIL start", name="kid", background=False)
    ctx = tool_ctx(services, parent)
    text, err = await call_tool(
        services, ctx, "send_message", {"to": "kid", "message": "please check X", "summary": "check X"}
    )
    assert not err and text == "delivered to kid"
    rows = await teams.mail.messages(kid.team_id, "kid")
    assert [(r.sender, r.recipient, r.read) for r in rows] == [("lead", "kid", True)]
    await turn(services, kid.session_id, "continue")
    last = gateway.requests_for("CHILD-MAIL")[-1]
    users = [text_of(m["content"]) for m in last.messages if m.get("role") == "user"]
    # injected mail is drained into the next turn's batch, marked out-of-band (spec §8.1/§8.8)
    mail = '[OUT-OF-BAND] <mail from="lead" summary="check X">please check X</mail>'
    assert any(u.endswith(mail) for u in users)
    server = await services.events.replay(0, frozenset({"server"}))
    assert any(e.type == "mailbox.message" and e.data["to"] == "kid" for e in server)
    peers, err = await call_tool(services, tool_ctx(services, kid.session_id), "list_peers", {})
    assert not err and "you are 'kid'" in peers and "- lead:" in peers and "- kid:" in peers
    text, err = await call_tool(services, ctx, "send_message", {"to": "ghost", "message": "x"})
    assert err and "no peer named 'ghost'" in text


async def test_broadcast_needs_short_summary_and_is_rate_limited(
    services: Services, gateway: RoutedGateway
) -> None:
    teams = teams_of(services)
    parent = await new_parent(services)
    a = await teams.spawn(parent, agent="default", prompt="CHILD-A", name="a", background=False)
    await teams.spawn(parent, agent="default", prompt="CHILD-B", name="b", background=False)
    rows = await teams.mail.send(a.team_id, "lead", "*", "all hands", "status update")
    assert sorted(r.recipient for r in rows) == ["a", "b"]
    with pytest.raises(TeamsError, match="summary"):
        await teams.mail.send(a.team_id, "lead", "*", "x", " ".join(["word"] * 11))
    for _ in range(4):
        await teams.mail.send(a.team_id, "a", "*", "ping", "ping")
    with pytest.raises(TeamsError, match="rate limit"):
        await teams.mail.send(a.team_id, "b", "*", "ping", "ping")


async def test_wait_agent_returns_when_child_finishes(services: Services, gateway: RoutedGateway) -> None:
    teams = teams_of(services)
    gate = asyncio.Event()
    gateway.route("CHILD-WAIT", Scripted(text="finished work"), gate=gate)
    parent = await new_parent(services)
    await teams.spawn(parent, agent="default", prompt="CHILD-WAIT", name="slow", background=True)
    ctx = tool_ctx(services, parent)
    text, err = await call_tool(services, ctx, "wait_agent", {"name": "slow", "timeout_ms": 10})
    assert err and "timeout_ms" in text  # schema bounds 1000..300000 ms
    text, err = await call_tool(services, ctx, "wait_agent", {"name": "slow", "timeout_ms": 1000})
    assert not err and "timed_out=True" in text and "running=True" in text
    gate.set()
    text, err = await call_tool(services, ctx, "wait_agent", {"name": "slow", "timeout_ms": 10_000})
    assert not err and "status=completed" in text and "finished work" in text


async def test_interrupt_agent_cancels_running_child(services: Services, gateway: RoutedGateway) -> None:
    teams = teams_of(services)
    gateway.route("CHILD-STUCK", Scripted(text="never"), gate=asyncio.Event())
    parent = await new_parent(services)
    m = await teams.spawn(parent, agent="default", prompt="CHILD-STUCK", name="stuck", background=True)
    await asyncio.sleep(0.2)
    assert services.coordinator.is_running(m.session_id)
    text, err = await call_tool(services, tool_ctx(services, parent), "interrupt_agent", {"name": "stuck"})
    assert not err and text == "interrupted stuck"
    assert m.status == "stopped" and not teams.is_running(m.session_id)
    members = await teams.members(m.team_id)
    assert members[0].status == "stopped"
    child_events = [t for t, _ in await events_of(services, m.session_id)]
    assert "run.aborted" in child_events
    failed = [d for t, d in await events_of(services, parent) if t == "subagent.failed"]
    assert failed and failed[0]["status"] == "stopped"


async def test_permission_ceiling_clamps_child_mode(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("auoreo.core.permissions.YOLO_FROZEN", True)
    write_agent(workdir, "wild", "permission_mode: yolo\n")
    services.harness.reload()
    assert clamp("yolo", "manual") == "manual" and clamp("smart", "acceptEdits") == "smart"
    assert clamp("acceptEdits", "plan") == "plan"

    # control: a top-level yolo agent runs as yolo
    solo = (await services.create_session(title="solo", agent="wild")).id
    await turn(services, solo, "hi")
    assert services.coordinator.engine_for(solo).mode == "yolo"  # type: ignore[union-attr]

    parent = await new_parent(services)  # default agent, settings mode manual
    m = await teams_of(services).spawn(parent, agent="wild", prompt="CEIL", name="w", background=False)
    assert m.status == "completed"
    engine = (await services.coordinator.runtime(m.session_id)).engine
    assert isinstance(engine, CeilingEngine) and engine.ceiling == "manual"
    assert engine.mode == "manual"  # the runner re-set it from the manifest: still clamped
    engine.mode = "yolo"
    assert engine.mode == "manual"


async def test_team_create_once_per_lead(services: Services) -> None:
    parent = await new_parent(services)
    ctx = tool_ctx(services, parent)
    text, err = await call_tool(services, ctx, "team_create", {"name": "alpha", "description": "d"})
    assert not err and "created team alpha" in text
    text, err = await call_tool(services, ctx, "team_create", {"name": "beta"})
    assert err and "one team per lead" in text
    other = await new_parent(services)
    text, err = await call_tool(services, tool_ctx(services, other), "team_create", {"name": "alpha"})
    assert err and "already exists" in text
    text, err = await call_tool(services, tool_ctx(services, other), "list_peers", {})
    assert err and "not in a team" in text


async def test_usage_rolls_up_children(services: Services, gateway: RoutedGateway) -> None:
    gateway.route("CHILD-USAGE", Scripted(text="counted"))
    parent = await new_parent(services)
    await turn(services, parent, "PARENT-USAGE hello")
    m = await teams_of(services).spawn(
        parent, agent="default", prompt="CHILD-USAGE", name="u", background=False
    )
    ledger = services.coordinator.runner.usage
    own = await ledger.by_session(parent)
    rolled = await ledger.by_session(parent, children=True)
    assert {r.session_id for r in own} == {parent}
    assert {r.session_id for r in rolled} == {parent, m.session_id}
    assert m.usage["calls"] == 1 and m.usage["prompt_tokens"] > 0
    total = await ledger.totals(await ledger.session_tree(parent))
    assert total["calls"] == len(rolled)


async def test_research_implement_verify_workflow_end_to_end(
    services: Services, gateway: RoutedGateway
) -> None:
    gateway.queue(
        Scripted(text="PLAN: change a.py"), Scripted(text="DONE: a.py:1 changed"), Scripted(text="approve")
    )
    sess = await services.create_session(title="wf", workflow="research-implement-verify")
    result = await turn(services, sess.id, "fix the bug")
    assert result.status == "completed" and result.text == "approve"
    children = [s for s in await services.list_sessions() if s.parent_id == sess.id]
    assert sorted(c.agent for c in children) == ["implementer", "planner", "reviewer"]
    main = [r for r in gateway.requests if r.metadata.get("purpose") not in {"title", "compaction"}]
    implement_prompt = text_of(main[1].messages[-1]["content"])
    verify_prompt = text_of(main[2].messages[-1]["content"])
    assert "PLAN: change a.py" in implement_prompt
    assert "PLAN: change a.py" in verify_prompt and "DONE: a.py:1 changed" in verify_prompt
    tools = {t["function"]["name"] for t in main[1].tools}
    assert "delegate_task" not in tools and "send_message" not in tools and "edit" in tools
    tools = {t["function"]["name"] for t in main[2].tools}
    assert "edit" not in tools and "bash" in tools and "delegate_task" not in tools
