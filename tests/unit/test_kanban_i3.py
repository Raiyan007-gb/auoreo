"""I3 kanban: WIP≤3 per worker, board hard boundary, worker-only verbs, dispatcher with
respawn guards / per-profile caps, worker log rotation (spec §16.6, §24.10, §25.11)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from auoreo.core.services import Services
from auoreo.gateway.fake import Scripted
from auoreo.teams.dispatcher import KanbanDispatcher
from auoreo.teams.kanban import KanbanError
from auoreo.teams.service import TeamsService
from auoreo.teams.worker_log import RotatingLog, worker_log_path
from tests.teams_helpers import RoutedGateway, call_tool, new_parent, tool_ctx


@pytest.fixture
def gateway() -> RoutedGateway:
    return RoutedGateway()


def teams_of(svc: Services) -> TeamsService:
    teams = svc.ext["teams"]
    assert isinstance(teams, TeamsService)
    return teams


def load(*names: str) -> Scripted:
    return Scripted(tool_calls=[("tool_search", {"query": n, "limit": 1}) for n in names])


async def test_wip_limit_per_worker(services: Services) -> None:
    kb = teams_of(services).kanban
    for i in range(5):
        await kb.create("wip", f"t{i}")
    for _ in range(3):
        assert await kb.claim("wip", "w1") is not None
    with pytest.raises(KanbanError, match="WIP limit"):
        await kb.claim("wip", "w1")
    assert await kb.claim("wip", "w2") is not None  # another worker is unaffected
    services.settings.i3.kanban.wip_per_worker = 4
    assert await kb.claim("wip", "w1") is not None


async def test_board_boundary_and_worker_verbs(services: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    kb = teams_of(services).kanban
    mine = await kb.create("alpha", "mine")
    other = await kb.create("beta", "theirs")
    worker = await new_parent(services, title="worker")
    ctx = tool_ctx(services, worker)
    kb.worker_boards[worker] = "alpha"
    text, err = await call_tool(services, ctx, "kanban_create", {"board": "beta", "title": "x"})
    assert err and "confined to board 'alpha'" in text
    text, err = await call_tool(services, ctx, "kanban_create", {"title": "pinned"})
    assert not err and "board=alpha" in text  # board defaults to the pinned one
    text, err = await call_tool(services, ctx, "kanban_show", {"task_id": other.id})
    assert err and "another board" in text
    text, err = await call_tool(services, ctx, "kanban_show", {"task_id": mine.id})
    assert not err, text
    for verb, args in (("kanban_list", {}), ("kanban_unblock", {"task_id": mine.id})):
        text, err = await call_tool(services, ctx, verb, args)
        assert err and "orchestrators" in text, verb
    kb.worker_boards.clear()

    # a worker process: AUOREO_KANBAN_BOARD pins every session
    monkeypatch.setenv("AUOREO_KANBAN_BOARD", "beta")
    lead = tool_ctx(services, await new_parent(services))
    text, err = await call_tool(services, lead, "kanban_complete", {"task_id": mine.id})
    assert err and "confined to 'beta'" in text
    text, err = await call_tool(services, lead, "kanban_list", {})
    assert err
    monkeypatch.delenv("AUOREO_KANBAN_BOARD")
    text, err = await call_tool(services, lead, "kanban_list", {"board": "beta"})
    assert not err and "theirs" in text


def test_worker_log_rotates(tmp_path: Path) -> None:
    path = worker_log_path(tmp_path, "b", "kw-1")
    rl = RotatingLog(path, max_bytes=4096)
    for i in range(80):
        rl.write({"type": "e", "i": i, "pad": "x" * 100})
    assert path.is_file() and path.with_name("kw-1.log.1").is_file()
    assert path.stat().st_size <= 4096


async def _drain(disp: KanbanDispatcher) -> None:
    for _ in range(200):
        if not disp.running:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("workers did not finish")


async def test_dispatcher_spawns_worker_and_sets_guards(services: Services, gateway: RoutedGateway) -> None:
    cfg = services.settings.i3.kanban
    cfg.boards, cfg.agent = ["jobs"], "implementer"
    kb = teams_of(services).kanban
    done = await kb.create("jobs", "KW-DONE build it")
    stuck = await kb.create("jobs", "KW-STUCK cannot finish")
    gateway.route(
        "KW-DONE",
        load("kanban_complete"),
        Scripted(tool_calls=[("kanban_complete", {"task_id": done.id, "summary": "built"})]),
        Scripted(text="finished"),
    )
    gateway.route("KW-STUCK", Scripted(text="I give up"))
    disp = KanbanDispatcher(services)
    spawned = await disp.tick()
    assert sorted(spawned) == sorted([done.id, stuck.id])
    await _drain(disp)
    assert (await kb.get(done.id)).status == "done"
    after = await kb.get(stuck.id)  # ended without completing: failure-counted, back to ready
    assert after.status == "ready" and after.failures == 1
    assert disp.guarded(done.id) == "recent success"
    assert disp.guarded(stuck.id) is None
    logs = list((services.settings.home / "kanban" / "logs" / "jobs").glob("kw-*.log"))
    assert len(logs) == 2 and all("worker.finished" in p.read_text() for p in logs)

    # a quota failure cools the task down for 5 minutes; a PR task for 24 h
    disp._guard(stuck.id, cfg.quota_cooldown_s, "quota cooldown")
    assert await disp.tick() == []
    assert disp.guarded(stuck.id, time.time() + cfg.quota_cooldown_s + 1) is None


async def test_dispatcher_profile_caps_and_pr_guard(services: Services, gateway: RoutedGateway) -> None:
    cfg = services.settings.i3.kanban
    cfg.boards, cfg.profile_caps = ["capped"], {"planner": 1}
    kb = teams_of(services).kanban
    gate = asyncio.Event()
    a = await kb.create("capped", "PR KW-CAP-A review", profile="planner")
    b = await kb.create("capped", "KW-CAP-B plan", profile="planner")
    gateway.route("KW-CAP-A", Scripted(text="a"), gate=gate)
    gateway.route("KW-CAP-B", Scripted(text="b"), gate=gate)
    disp = KanbanDispatcher(services)
    assert await disp.tick() == [a.id]  # planner cap = 1
    assert await disp.tick() == []
    gate.set()
    await _drain(disp)
    assert disp.guarded(a.id) == "PR task"
    assert await disp.tick() == [b.id]
    await _drain(disp)
