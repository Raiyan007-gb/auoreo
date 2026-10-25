"""G4b: delegate_task `tasks[]` batch mode (concurrency 3, joined notifications)."""

from __future__ import annotations

import asyncio

import pytest

from auoreo.core.services import Services
from auoreo.gateway.fake import Scripted
from tests.teams_helpers import RoutedGateway, call_tool, new_parent, tool_ctx


@pytest.fixture
def gateway() -> RoutedGateway:
    return RoutedGateway()


async def spawned(svc: Services, parent: str) -> int:
    evs = await svc.events.replay(0, frozenset({f"session:{parent}"}), limit=100_000)
    return sum(e.type == "subagent.started" for e in evs)


async def test_foreground_batch_joins_results_in_order(services: Services, gateway: RoutedGateway) -> None:
    for i in range(4):
        gateway.route(f"BATCH-{i}", Scripted(text=f"answer {i}"))
    parent = await new_parent(services)
    tasks = [{"goal": f"BATCH-{i} do part {i}", "name": f"part_{i}"} for i in range(4)]
    text, err = await call_tool(services, tool_ctx(services, parent), "delegate_task", {"tasks": tasks})
    assert not err and text.startswith("batch of 4 tasks: 4 completed (concurrency 3)")
    positions = [text.index(f"<result>answer {i}</result>") for i in range(4)]
    assert positions == sorted(positions) and text.count("<task-notification>") == 4


async def test_batch_runs_at_most_n_at_once(services: Services, gateway: RoutedGateway) -> None:
    services.settings.tools.delegate_batch_concurrency = 2
    gate = asyncio.Event()
    for i in range(3):
        gateway.route(f"SLOWB-{i}", Scripted(text=f"slow {i}"), gate=gate)
    parent = await new_parent(services)
    tasks = [{"goal": f"SLOWB-{i}", "name": f"s_{i}"} for i in range(3)]
    run = asyncio.create_task(
        call_tool(services, tool_ctx(services, parent), "delegate_task", {"tasks": tasks})
    )
    await asyncio.sleep(0.4)
    assert await spawned(services, parent) == 2  # the third waits for a free slot
    gate.set()
    text, err = await asyncio.wait_for(run, 20)
    assert not err and "3 completed (concurrency 2)" in text


async def test_background_batch(services: Services, gateway: RoutedGateway) -> None:
    gateway.route("BGB-0", Scripted(text="bg zero"))
    gateway.route("BGB-1", Scripted(text="bg one"))
    parent = await new_parent(services)
    tasks = [{"goal": "BGB-0", "name": "bg_0"}, {"goal": "BGB-1", "name": "bg_1"}]
    text, err = await call_tool(
        services, tool_ctx(services, parent), "delegate_task", {"tasks": tasks, "background": True}
    )
    assert not err and text.startswith("Started 2/2 background tasks") and "'bg_1'" in text
    teams = services.ext["teams"]
    team = await teams.team_of(parent)
    done = await teams.wait(team.id, "bg_1", 10_000)
    assert done["result"] == "bg one"


async def test_batch_validation(services: Services, gateway: RoutedGateway) -> None:
    parent = await new_parent(services)
    ctx = tool_ctx(services, parent)
    text, err = await call_tool(services, ctx, "delegate_task", {"tasks": [{"goal": "a", "name": "x"}] * 2})
    assert err and "duplicate task names" in text
    text, err = await call_tool(services, ctx, "delegate_task", {"goal": "a", "tasks": [{"goal": "b"}]})
    assert err and "not both" in text
    text, err = await call_tool(services, ctx, "delegate_task", {})
    assert err and "not both" in text


async def test_batch_reports_per_task_failures(services: Services, gateway: RoutedGateway) -> None:
    gateway.route("OKB", Scripted(text="fine"))
    parent = await new_parent(services)
    tasks = [{"goal": "OKB", "name": "good"}, {"goal": "x", "name": "bad", "agent": "no_such_agent"}]
    text, err = await call_tool(services, tool_ctx(services, parent), "delegate_task", {"tasks": tasks})
    assert not err and "1 completed" in text and '<task-error name="bad">' in text and "unknown agent" in text
