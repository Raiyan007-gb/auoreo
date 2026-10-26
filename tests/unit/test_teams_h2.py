"""H2 teams gaps: depth-derived leaf role, root max_cost, delegate_task actions, fork, pause,
live logs, heartbeat/staleness, budgets, resume_agent, stop_task (spec §16.1–§16.5, §25.11/15)."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import pytest

from auoreo.core.db import UsageRow
from auoreo.core.services import Services
from auoreo.gateway.fake import Scripted
from auoreo.gateway.types import LLMRequest, text_of
from auoreo.teams.ceiling import LEAF_DENIED, CeilingEngine, role_for
from auoreo.teams.monitor import BUDGET_EXCEEDED, Budget, ChildMonitor, live_log_path
from auoreo.teams.service import TeamsError, TeamsService
from tests.teams_helpers import RoutedGateway, call_tool, first_user, new_parent, tool_ctx, turn


@pytest.fixture
def gateway() -> RoutedGateway:
    return RoutedGateway()


def teams_of(svc: Services) -> TeamsService:
    teams = svc.ext["teams"]
    assert isinstance(teams, TeamsService)
    return teams


def tool_names(req: LLMRequest) -> set[str]:
    return {str((t.get("function") or {}).get("name") or t.get("name")) for t in req.tools}


async def events_of(svc: Services, sid: str) -> list[tuple[str, dict[str, Any]]]:
    evs = await svc.events.replay(0, frozenset({f"session:{sid}"}), limit=100_000)
    return [(e.type, e.data) for e in evs]


async def add_cost(svc: Services, session_id: str, usd: float, tokens: int = 10) -> None:
    async with svc.db.session() as s:
        s.add(
            UsageRow(
                session_id=session_id,
                run_id="r",
                model="fake",
                prompt_tokens=tokens,
                completion_tokens=tokens,
                cost_usd=usd,
            )
        )
        await s.commit()


# ---------------------------------------------------------------- §16.3 roles
def test_role_is_depth_derived() -> None:
    assert role_for(False, 1) == "leaf"
    assert role_for(True, 1) == "orchestrator"
    assert role_for(True, 2) == "leaf"


async def test_leaf_child_has_denied_tools_hidden_and_refused(
    services: Services, gateway: RoutedGateway
) -> None:
    gateway.route("CHILD-LEAF", Scripted(text="leaf done"))
    teams = teams_of(services)
    parent = await new_parent(services)
    kid = await teams.spawn(parent, agent="default", prompt="CHILD-LEAF go", name="leafy", background=False)
    assert kid.status == "completed" and kid.role == "leaf" and teams.roles[kid.session_id] == "leaf"

    # hidden from the child's tool list ...
    offered = tool_names(gateway.requests_for("CHILD-LEAF")[0])
    assert offered and not offered & set(LEAF_DENIED)

    # ... and refused by the executor through the child's own engine (a floor, not a schema omission)
    engine = services.coordinator.engine_for(kid.session_id)
    assert (
        isinstance(engine, CeilingEngine)
        and {"delegate", "memory", "send_message", "cronjob"} <= engine.denied
    )
    ctx = tool_ctx(services, kid.session_id)
    ctx.services["engine"] = engine
    for name, args in (
        ("delegate_task", {"goal": "x"}),
        ("send_message", {"to": "lead", "message": "hi"}),
        ("memory", {"operations": [{"action": "add", "target": "fact", "content": "x"}]}),
        ("cronjob", {"action": "list"}),
    ):
        text, err = await call_tool(services, ctx, name, args)
        assert err and "not available to leaf subagents" in text, (name, text)

    # the delegate_task handler refuses too, and a leaf cannot spawn programmatically
    text, err = await call_tool(services, tool_ctx(services, kid.session_id), "delegate_task", {"goal": "x"})
    assert err and "leaf" in text
    with pytest.raises(TeamsError, match="leaf"):
        await teams.spawn(kid.session_id, agent="default", prompt="x", name="grandkid", background=True)


async def test_orchestrator_child_keeps_delegate_task(services: Services, gateway: RoutedGateway) -> None:
    services.settings.teams_orchestrator_enabled = True
    gateway.route("CHILD-ORCH", Scripted(text="ok"))
    teams = teams_of(services)
    parent = await new_parent(services)
    kid = await teams.spawn(parent, agent="default", prompt="CHILD-ORCH go", name="orch", background=False)
    assert kid.role == "orchestrator"
    assert "delegate_task" in tool_names(gateway.requests_for("CHILD-ORCH")[0])
    engine = services.coordinator.engine_for(kid.session_id)
    assert isinstance(engine, CeilingEngine) and not engine.denied


# ---------------------------------------------------------------- §25.15 root max_cost
async def test_child_spend_counts_toward_root_max_cost(services: Services, gateway: RoutedGateway) -> None:
    gateway.route("CHILD-COST", Scripted(text="spent"), Scripted(text="should not run"))
    teams = teams_of(services)
    parent = await new_parent(services)
    kid = await teams.spawn(parent, agent="default", prompt="CHILD-COST go", name="spender", background=False)
    assert kid.status == "completed"
    await add_cost(services, kid.session_id, 2.0)
    services.settings.max_cost_usd = 1.0
    ledger = services.coordinator.runner.usage
    assert await ledger.tree_cost(parent) == pytest.approx(2.0)
    assert await ledger.over_budget(parent, 1.0)  # the parent itself spent nothing

    with pytest.raises(TeamsError, match="BUDGET"):
        await teams.spawn(parent, agent="default", prompt="x", name="second", background=True)
    # a further turn of the existing child is blocked before it reaches the model
    before = len(gateway.requests_for("CHILD-COST"))
    again = await teams.control.resume(parent, "spender", "more", background=False)
    assert again.status == "failed" and again.error and again.error["code"] == "BUDGET"
    assert len(gateway.requests_for("CHILD-COST")) == before
    # and the parent's own next turn stops with BUDGET after one model call
    result = await turn(services, parent, "PARENT-COST next")
    assert result.status == "blocked" and result.error["code"] == "BUDGET"


# ---------------------------------------------------------------- §16.1 actions / pause / fork
async def test_delegate_task_list_steer_stop_actions(services: Services, gateway: RoutedGateway) -> None:
    gate = asyncio.Event()
    gateway.route("CHILD-ACT", Scripted(text="acted"), gate=gate)
    teams = teams_of(services)
    parent = await new_parent(services)
    ctx = tool_ctx(services, parent)
    text, err = await call_tool(
        services, ctx, "delegate_task", {"goal": "CHILD-ACT go", "name": "actor", "background": True}
    )
    assert not err, text
    member = next(iter(teams.members_live.values()))
    for _ in range(100):
        if services.coordinator.is_running(member.session_id):
            break
        await asyncio.sleep(0.02)

    text, err = await call_tool(services, ctx, "delegate_task", {"action": "list"})
    assert not err and "actor" in text and member.session_id in text and "running=True" in text

    text, err = await call_tool(
        services, ctx, "delegate_task", {"action": "steer", "subagent_id": "actor", "message": "focus on X"}
    )
    assert not err and "steered actor" in text
    text, err = await call_tool(services, ctx, "delegate_task", {"action": "steer", "subagent_id": "nobody"})
    assert err and "needs message" in text

    # pause refuses new spawns but list/stop still work
    await teams.control.set_paused(True)
    text, err = await call_tool(services, ctx, "delegate_task", {"goal": "x", "name": "blocked_kid"})
    assert err and "paused" in text
    text, err = await call_tool(services, ctx, "delegate_task", {"action": "list"})
    assert not err and "delegation is paused" in text
    text, err = await call_tool(
        services, ctx, "delegate_task", {"action": "stop", "subagent_id": member.session_id}
    )
    assert not err and "stopped actor" in text
    assert member.status == "stopped"
    gate.set()
    await teams.control.set_paused(False)
    kinds = [t for t, _ in await events_of(services, parent)]
    assert "subagent.steered" in kinds and "subagent.start" in kinds


async def test_pause_switch_from_settings(services: Services) -> None:
    services.settings.teams2.delegation.pause = True
    teams = teams_of(services)
    parent = await new_parent(services)
    with pytest.raises(TeamsError, match="paused"):
        await teams.spawn(parent, agent="default", prompt="x", name="nope", background=True)
    assert teams.control.paused


async def test_fork_mode_seeds_parent_context(services: Services, gateway: RoutedGateway) -> None:
    gateway.route("CHILD-FORK", Scripted(text="forked ok"))
    gateway.route("PARENT-HIST", Scripted(text="the secret plan is BLUE"))
    teams = teams_of(services)
    parent = await new_parent(services)
    await turn(services, parent, "PARENT-HIST what is the plan?")
    kid = await teams.spawn(
        parent, agent="default", prompt="CHILD-FORK continue", name="forky", background=False, mode="fork"
    )
    assert kid.status == "completed"
    seed = first_user(gateway.requests_for("CHILD-FORK")[0])
    assert "<parent-context>" in seed and "the secret plan is BLUE" in seed and "PARENT-HIST" in seed
    assert seed.index("<parent-context>") < seed.index("CHILD-FORK continue")
    with pytest.raises(TeamsError, match="unknown mode"):
        await teams.spawn(parent, agent="default", prompt="x", name="bad_mode", background=True, mode="clone")


# ---------------------------------------------------------------- live log / heartbeat / stale
async def test_live_log_written_for_child(services: Services, gateway: RoutedGateway) -> None:
    gateway.route("CHILD-LOG", Scripted(text="logged"))
    teams = teams_of(services)
    parent = await new_parent(services)
    kid = await teams.spawn(parent, agent="default", prompt="CHILD-LOG go", name="logger", background=False)
    assert re.fullmatch(r"sa-1-[0-9a-f]{8}", kid.subagent_id)
    path = live_log_path(services.settings.home, parent, kid.subagent_id, 1)
    live = services.settings.home / "sessions" / parent / "delegation" / "live"
    assert path == live / kid.subagent_id / "task-1.log"
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    types = {line["type"] for line in lines}
    assert "run.started" in types and "message.delta" not in types


async def test_stale_child_warns_then_fails(services: Services, gateway: RoutedGateway) -> None:
    cfg = services.settings.teams2.delegation
    cfg.poll_s, cfg.heartbeat_s, cfg.stale_warn_s, cfg.stale_fail_s = 0.05, 0.1, 0.2, 0.6
    gate = asyncio.Event()  # never set: the child hangs inside its model call
    gateway.route("CHILD-STALE", Scripted(text="never"), gate=gate)
    teams = teams_of(services)
    parent = await new_parent(services)
    kid = await teams.spawn(parent, agent="default", prompt="CHILD-STALE go", name="stale", background=False)
    assert kid.status == "failed" and kid.error and kid.error["code"] == "STALE"
    progress = [d for t, d in await events_of(services, parent) if t == "subagent.progress"]
    assert any(d.get("stale") == "warn" for d in progress)
    assert any("idle_s" in d for d in progress)
    assert parent in teams.last_activity
    gate.set()


# ---------------------------------------------------------------- budgets
def test_budget_parsing_and_limits() -> None:
    b = Budget.of({"max_tokens": 100, "max_usd": 0.5, "timeout_ms": 2000})
    assert b.exceeded({"prompt_tokens": 50, "completion_tokens": 49, "cost_usd": 0.1}) is None
    assert "max_tokens" in (b.exceeded({"prompt_tokens": 60, "completion_tokens": 40}) or "")
    assert "max_usd" in (b.exceeded({"cost_usd": 0.5}) or "")
    assert Budget.of(None) == Budget()


async def test_budget_monitor_grace_then_stop(services: Services, gateway: RoutedGateway) -> None:
    gateway.route("CHILD-BUD", Scripted(text="tiny"))
    teams = teams_of(services)
    parent = await new_parent(services)
    kid = await teams.spawn(parent, agent="default", prompt="CHILD-BUD go", name="budgeted", background=False)
    mon = ChildMonitor(teams, kid, Budget(max_tokens=50))
    await add_cost(services, kid.session_id, 0.0, tokens=30)  # 60 tokens ≥ 50
    await mon.tick()
    assert mon.budget_hit and mon.verdict is None  # grace: one summarise-now note is injected
    rt = await services.coordinator.runtime(kid.session_id)
    assert any("delegation budget" in n for n in rt.inbox.next_step)
    await add_cost(services, kid.session_id, 0.0)
    await add_cost(services, kid.session_id, 0.0)
    await mon.tick()
    assert mon.verdict and mon.verdict["code"] == BUDGET_EXCEEDED
    progress = [d for t, d in await events_of(services, parent) if t == "subagent.progress"]
    assert any(d.get("budget") == "grace" for d in progress)
    assert any(d.get("stopped") == BUDGET_EXCEEDED for d in progress)


async def test_budget_timeout_ms_bounds_the_child(services: Services, gateway: RoutedGateway) -> None:
    gate = asyncio.Event()
    gateway.route("CHILD-TMO", Scripted(text="late"), gate=gate)
    teams = teams_of(services)
    parent = await new_parent(services)
    kid = await teams.spawn(
        parent,
        agent="default",
        prompt="CHILD-TMO",
        name="slowpoke",
        background=False,
        budget={"timeout_ms": 1000},
    )
    assert kid.status == "failed" and kid.error and kid.error["code"] == "TIMEOUT"
    gate.set()


# ---------------------------------------------------------------- resume_agent / stop_task
async def test_resume_agent_wakes_finished_child(services: Services, gateway: RoutedGateway) -> None:
    gateway.route("CHILD-RES", Scripted(text="first answer"), Scripted(text="second answer"))
    teams = teams_of(services)
    parent = await new_parent(services)
    kid = await teams.spawn(
        parent, agent="default", prompt="CHILD-RES start", name="resumable", background=False
    )
    assert kid.text == "first answer"
    ctx = tool_ctx(services, parent)
    text, err = await call_tool(
        services, ctx, "resume_agent", {"task_id": "resumable", "prompt": "now part two"}
    )
    assert not err and "second answer" in text and "<status>completed</status>" in text
    req = gateway.requests_for("CHILD-RES")[-1]
    users = [text_of(m["content"]) for m in req.messages if m.get("role") == "user"]
    assert users[-1].endswith("now part two") and "CHILD-RES start" in users[0]  # same session, history kept
    assert "subagent.resumed" in [t for t, _ in await events_of(services, parent)]
    text, err = await call_tool(services, ctx, "resume_agent", {"task_id": "ghost", "prompt": "x"})
    assert err and "no subagent 'ghost'" in text


async def test_stop_task_stops_running_child(services: Services, gateway: RoutedGateway) -> None:
    gate = asyncio.Event()
    gateway.route("CHILD-STOP", Scripted(text="x"), gate=gate)
    teams = teams_of(services)
    parent = await new_parent(services, agent="coordinator")
    kid = await teams.spawn(parent, agent="default", prompt="CHILD-STOP", name="stoppable", background=True)
    for _ in range(100):
        if services.coordinator.is_running(kid.session_id):
            break
        await asyncio.sleep(0.02)
    text, err = await call_tool(
        services,
        tool_ctx(services, parent, "coordinator"),
        "stop_task",
        {"task_id": kid.session_id, "reason": "enough"},
    )
    assert not err and "stopped stoppable" in text and kid.status == "stopped"
    assert "stop_task" in services.harness.agent("coordinator").manifest.tools.allow
    gate.set()
