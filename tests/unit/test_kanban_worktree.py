"""M7 W4.2: the kanban dispatcher gives every worker its own worktree (`m7.orch.worktrees`):
the task carries the path and branch as an attachment, a re-dispatch resumes the same branch,
a worktree failure unclaims the task, and `worktree gc` keeps a claimed task's worktree."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from auoreo.core.services import Services
from auoreo.gateway.fake import Scripted
from auoreo.runtime import cwd as session_cwd
from auoreo.runtime import worktrees as wt
from auoreo.teams.dispatcher import KanbanDispatcher
from auoreo.teams.worktree_spawn import protected_paths
from tests.teams_helpers import RoutedGateway
from tests.worktree_helpers import flags_on, git, git_init, worktree_paths


@pytest.fixture
def gateway() -> RoutedGateway:
    return RoutedGateway()


def _load(name: str) -> Scripted:
    return Scripted(tool_calls=[("tool_search", {"query": name, "limit": 1})])


async def _drain(disp: KanbanDispatcher) -> None:
    for _ in range(400):
        if not disp.running:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("workers did not finish")


async def _attachments(kb: Any, task_id: int) -> list[dict[str, Any]]:
    rows = await kb.comments(task_id, structured=True)
    return [json.loads(r.text) for r in rows if r.author == "__attach__"]


def _setup(services: Services, workdir: Path) -> Any:
    git_init(workdir)
    flags_on(services)
    cfg = services.settings.i3.kanban
    cfg.boards, cfg.agent = ["jobs"], "implementer"
    return services.ext["teams"].kanban


async def test_flag_off_workers_share_the_checkout(
    services: Services, workdir: Path, gateway: RoutedGateway
) -> None:
    kb = _setup(services, workdir)
    services.settings.m7.orch.worktrees = False
    task = await kb.create("jobs", "KOFF plain")
    gateway.route("KOFF", Scripted(text="nothing"))
    disp = KanbanDispatcher(services)
    assert await disp.tick() == [task.id]
    (worker,) = disp.running.values()
    rt = await services.coordinator.runtime(worker.session_id)
    assert rt.cwd is None
    await _drain(disp)
    assert "git worktree" not in str(rt.history[0]["content"])
    assert await _attachments(kb, task.id) == [] and len(worktree_paths(workdir)) == 1


async def test_two_workers_two_worktrees_and_a_redispatch_resumes(
    services: Services, workdir: Path, gateway: RoutedGateway
) -> None:
    kb = _setup(services, workdir)
    done = await kb.create("jobs", "KWT-DONE build it")
    stuck = await kb.create("jobs", "KWT-STUCK cannot finish")
    gateway.route(
        "KWT-DONE",
        _load("kanban_complete"),
        Scripted(tool_calls=[("kanban_complete", {"task_id": done.id, "summary": "built"})]),
        Scripted(text="finished"),
    )
    gateway.route("KWT-STUCK", Scripted(text="I give up"))
    disp = KanbanDispatcher(services)
    assert sorted(await disp.tick()) == sorted([done.id, stuck.id])
    root = session_cwd.orch_root(services.settings, services.cwd)
    base = git(workdir, "rev-parse", "HEAD")
    by_task = {w.task_id: w for w in disp.running.values()}
    for task in (done, stuck):
        path, branch = root / f"kjobs-{task.id}", f"auoreo/kjobs-{task.id}"
        assert (await _attachments(kb, task.id)) == [
            {"ref": str(path), "note": f"branch={branch} base={base}", "by": by_task[task.id].name}
        ]
        sid = by_task[task.id].session_id
        assert (await services.coordinator.runtime(sid)).cwd == path
        evs = await services.events.replay(0, frozenset({f"session:{sid}"}), limit=1000)
        (created,) = [e.data for e in evs if e.type == "worktree.created"]
        assert created["path"] == str(path) and created["branch"] == branch
        assert (created["task_id"], created["board"], created["resumed"]) == (task.id, "jobs", False)
        assert created["base"] == base
        assert git(path, "branch", "--show-current") == branch
    await _drain(disp)
    assert (await kb.get(done.id)).status == "done" and (await kb.get(stuck.id)).status == "ready"
    assert len(worktree_paths(workdir)) == 3  # nothing is removed when a task ends

    # a worker's commit survives the re-dispatch: same folder, same branch, no reset
    tree = root / f"kjobs-{stuck.id}"
    (tree / "half.txt").write_text("half done\n", encoding="utf-8")
    git(tree, "add", "half.txt")
    git(tree, "commit", "--quiet", "-m", "half")
    tip = git(workdir, "rev-parse", f"auoreo/kjobs-{stuck.id}")
    gateway.route("KWT-STUCK", Scripted(text="still stuck"))
    again = KanbanDispatcher(services)  # a restart: no in-memory state
    assert await again.tick() == [stuck.id]
    (worker,) = again.running.values()
    assert (await services.coordinator.runtime(worker.session_id)).cwd == tree
    evs = await services.events.replay(0, frozenset({f"session:{worker.session_id}"}), limit=1000)
    assert [e.data["resumed"] for e in evs if e.type == "worktree.created"] == [True]
    assert git(workdir, "rev-parse", f"auoreo/kjobs-{stuck.id}") == tip
    assert len(await _attachments(kb, stuck.id)) == 1  # the mapping is recorded once
    await _drain(again)


async def test_worker_prompt_asks_for_a_commit(
    services: Services, workdir: Path, gateway: RoutedGateway
) -> None:
    kb = _setup(services, workdir)
    task = await kb.create("jobs", "KPROMPT do it")
    gateway.route("KPROMPT", Scripted(text="ok"))
    disp = KanbanDispatcher(services)
    await disp.tick()
    (worker,) = disp.running.values()
    await _drain(disp)
    rt = await services.coordinator.runtime(worker.session_id)
    first = str(rt.history[0]["content"])
    assert "commit your work on the current branch" in first.lower() and f"kjobs-{task.id}" in first


async def test_worktree_failure_unclaims_the_task(
    services: Services, workdir: Path, gateway: RoutedGateway
) -> None:
    kb = _setup(services, workdir)
    services.settings.m7.orch.max_worktrees = 1
    first = await kb.create("jobs", "KCAP-A one")
    second = await kb.create("jobs", "KCAP-B two")
    gate = asyncio.Event()
    gateway.route("KCAP-A", Scripted(text="a"), gate=gate)
    disp = KanbanDispatcher(services)
    assert await disp.tick() == [first.id]
    row = await kb.get(second.id)
    assert (row.status, row.worker, row.session_id, row.failures) == ("ready", None, None, 0)
    assert disp.guarded(second.id) == "worktree failed" and len(disp.running) == 1
    assert await _attachments(kb, second.id) == []
    gate.set()
    await _drain(disp)


async def test_gc_keeps_the_worktree_of_a_claimed_task(
    services: Services, workdir: Path, gateway: RoutedGateway
) -> None:
    kb = _setup(services, workdir)
    task = await kb.create("jobs", "KGC wait")
    gate = asyncio.Event()
    gateway.route("KGC", Scripted(text="late"), gate=gate)
    disp = KanbanDispatcher(services)
    await disp.tick()
    root = session_cwd.orch_root(services.settings, services.cwd)
    tree = root / f"kjobs-{task.id}"
    keep = await protected_paths(services.db)
    assert keep == {tree}
    _top, items = await wt.plan_gc(workdir, root, 0, protected=keep)
    assert [(i.path, i.action, i.reason) for i in items] == [
        (tree.resolve(), "keep", "task claimed or running")
    ]
    _top, items = await wt.plan_gc(workdir, root, 0)  # unprotected, it would go (stale at 0 days)
    assert [i.action for i in items] == ["remove"]
    gate.set()
    await _drain(disp)
    assert await protected_paths(services.db) == set()  # the task is no longer claimed


def test_gc_cli_covers_the_orchestrator_root(
    workdir: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from auoreo.cli.app import app
    from auoreo.core.settings import Settings

    git_init(workdir)
    monkeypatch.chdir(workdir)
    root = session_cwd.orch_root(Settings(home=home), workdir)
    tree = asyncio.run(wt.task_worktree(workdir, root, "kjobs-9")).path
    runner = CliRunner()
    off = runner.invoke(app, ["worktree", "gc", "--dry-run", "--json"])
    assert off.exit_code == 0 and json.loads(off.stdout) == []  # flag off: today's single root
    monkeypatch.setenv("AUOREO_M7", '{"orch": {"session_cwd": true, "worktrees": true}}')
    on = runner.invoke(app, ["worktree", "gc", "--dry-run", "--json"])
    assert on.exit_code == 0, on.output
    assert [Path(r["path"]) for r in json.loads(on.stdout)] == [tree.resolve()]
