"""M7 W4 Wave 2, phase 4: the harness records a worker branch's tip when the worker ends
(`review.requested`, D2), and a merge-conflict task, once unblocked, is dispatched into the
conflicting branch's worktree."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from auoreo.core.services import Services
from auoreo.gateway.fake import Scripted
from auoreo.teams import merge_records
from auoreo.teams.dispatcher import KanbanDispatcher
from tests.merge_helpers import commit, events, init_with_identity, recorded_worktree
from tests.teams_helpers import RoutedGateway, call_tool, new_parent, tool_ctx
from tests.worktree_helpers import flags_on, git


@pytest.fixture
def gateway() -> RoutedGateway:
    return RoutedGateway()


async def _drain(disp: KanbanDispatcher) -> None:
    for _ in range(400):
        if not disp.running:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("workers did not finish")


async def test_record_tip_is_a_no_op_without_the_flag(services: Services, workdir: Path) -> None:
    s = await recorded_worktree(services)
    sha = commit(s.tree.path, {"a.txt": "a\n"})
    services.settings.m7.orch.merge = False
    await merge_records.record_tip(services, s.worker)
    assert await events(services, "review.requested") == []
    services.settings.m7.orch.merge = True
    await merge_records.record_tip(services, s.worker)
    ((kind, aggregate, data),) = await events(services, "review.requested")
    assert aggregate == s.worker and data["sha"] == sha and data["branch"] == s.tree.branch
    assert data["commits"] == 1 and data["files"] == 1 and data["task_ref"] == "kanban:default#1"
    await merge_records.record_tip(services, "ses_without_a_worktree")  # never raises
    assert len(await events(services, "review.requested")) == 1


async def test_dispatched_worker_end_records_its_tip(
    services: Services, workdir: Path, gateway: RoutedGateway
) -> None:
    init_with_identity(workdir)
    flags_on(services)
    services.settings.m7.orch.merge = True
    cfg = services.settings.i3.kanban
    cfg.boards, cfg.agent = ["jobs"], "implementer"
    kb = services.ext["teams"].kanban
    task = await kb.create("jobs", "KREC record me")
    gateway.route("KREC", Scripted(text="did nothing"))
    disp = KanbanDispatcher(services)
    assert await disp.tick() == [task.id]
    (worker,) = disp.running.values()
    await _drain(disp)
    got = [(a, d) for _, a, d in await events(services, "review.requested")]
    assert got and got[0][0] == worker.session_id
    assert (
        got[0][1]["branch"] == f"auoreo/kjobs-{task.id}" and got[0][1]["task_ref"] == f"kanban:jobs#{task.id}"
    )
    created = [d for _, _, d in await events(services, "worktree.created")]
    assert created[0]["task_ref"] == f"kanban:jobs#{task.id}"


async def test_delegate_child_end_records_its_tip(
    services: Services, workdir: Path, gateway: RoutedGateway
) -> None:
    init_with_identity(workdir)
    flags_on(services)
    services.settings.m7.orch.merge = True
    parent = await new_parent(services)
    gateway.route("DREC", Scripted(text="done"))
    _, err = await call_tool(
        services,
        tool_ctx(services, parent),
        "delegate_task",
        {"goal": "DREC x", "name": "drec", "worktree": True},
    )
    assert not err
    ((_, aggregate, data),) = await events(services, "review.requested")
    assert data["task_ref"] == f"delegate:{parent}/drec" and data["branch"].startswith("auoreo/drec-")
    (created,) = [d for _, _, d in await events(services, "worktree.created")]
    assert created["task_ref"] == f"delegate:{parent}/drec"
    assert aggregate != parent


async def test_unblocked_conflict_task_runs_in_the_conflicting_worktree(
    services: Services, workdir: Path, gateway: RoutedGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auoreo.teams import merge_flow
    from tests.merge_helpers import call

    monkeypatch.setattr(merge_flow, "available", lambda: True)
    s = await recorded_worktree(services, "kjobs-1", task_ref="kanban:jobs#1")
    kb = services.ext["teams"].kanban
    await kb.create("jobs", "the original task")
    sha = commit(s.tree.path, {"README.md": "theirs\n"})
    commit(s.repo, {"README.md": "ours\n"})
    res = await call(services, {"branch": s.tree.branch, "sha": sha})
    assert res.is_error and "conflict" in res.content
    (data,) = [d for _, _, d in await events(services, "merge.conflicts")]
    conflict = data["conflict_task"]
    assert (await kb.get(conflict)).board == "jobs"
    cfg = services.settings.i3.kanban
    cfg.boards, cfg.agent = ["jobs"], "implementer"
    disp = KanbanDispatcher(services)
    assert conflict not in await disp.tick()  # blocked: nobody starts on it unprompted
    await kb.unblock(conflict)
    gateway.route("Resolve merge conflicts", Scripted(text="resolved"))
    assert conflict in await disp.tick()
    worker = next(w for w in disp.running.values() if w.task_id == conflict)
    rt = await services.coordinator.runtime(worker.session_id)
    assert rt.cwd is not None and rt.cwd.resolve() == s.tree.path.resolve()
    assert git(s.tree.path, "branch", "--show-current") == s.tree.branch
    await _drain(disp)


async def test_flag_off_dispatcher_ignores_conflict_records(services: Services, workdir: Path) -> None:
    init_with_identity(workdir)
    flags_on(services)
    disp = KanbanDispatcher(services)
    row: Any = type("Row", (), {"board": "jobs", "id": 7})()
    assert await disp._worktree_of(row) == ("kjobs-7", None)
