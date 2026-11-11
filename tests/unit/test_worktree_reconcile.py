"""M7 W4 Wave 2, phase 5: `auoreo worktree gc --reconcile`, its journal under
`<home>/worktrees/`, and the server pass that turns the journal into `worktree.removed` and
`worktree.gc` events (the CLI never writes an event)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.runtime import cwd as session_cwd
from auoreo.runtime import worktrees as wt
from auoreo.teams import worktree_reconcile as rc
from auoreo.teams import worktree_spawn
from tests.merge_helpers import commit, events, init_with_identity
from tests.worktree_helpers import branches, flags_on, git


@pytest.fixture(autouse=True)
def _no_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wt, "MERGED_GRACE_S", -1.0)  # a fresh worktree may count as merged here


async def _setup(svc: Services) -> dict[str, Path]:
    init_with_identity(svc.cwd)
    flags_on(svc)
    root = session_cwd.orch_root(svc.settings, svc.cwd)
    merged = (await wt.task_worktree(svc.cwd, root, "kdefault-1")).path
    missing = (await wt.task_worktree(svc.cwd, root, "kdefault-2")).path
    running = (await wt.task_worktree(svc.cwd, root, "child-ab12")).path
    shutil.rmtree(missing)
    git(svc.cwd, "branch", "auoreo/stray")  # merged, no worktree
    git(svc.cwd, "branch", "auoreo/unmerged")
    git(svc.cwd, "checkout", "--quiet", "auoreo/unmerged")
    commit(svc.cwd, {"u.txt": "u\n"})
    git(svc.cwd, "checkout", "--quiet", "main")
    return {"root": root, "merged": merged, "missing": missing, "running": running}


async def _plan(svc: Services, protected: set[Path], *, restore: bool = False) -> tuple[Path, list, list]:
    paths = await rc.records(svc.db)
    top, items = await wt.plan_gc(
        svc.cwd, session_cwd.orch_root(svc.settings, svc.cwd), 3, protected=protected
    )
    return top, items, await rc.plan(top, items, paths, protected, restore=restore)


async def test_reconcile_plan_apply_and_journal(services: Services, workdir: Path) -> None:
    p = await _setup(services)
    top, items, findings = await _plan(services, {p["running"]})
    acts = {(i.path.name, i.action) for i in items}
    assert (
        ("kdefault-1", "remove") in acts
        and ("kdefault-2", "prune") in acts
        and ("child-ab12", "keep") in acts
    )
    kinds = {(f.branch, f.kind, f.action) for f in findings}
    assert ("auoreo/stray", "merged_branch", "delete_branch") in kinds
    assert ("auoreo/kdefault-1", "merged_branch", "delete_branch") in kinds  # its worktree goes too
    assert ("auoreo/unmerged", "unmerged_branch", "keep") in kinds
    assert not any(f.branch == "auoreo/child-ab12" for f in findings)  # the running child's branch
    lines = await rc.apply(top, items, findings, services.settings.home)
    left = branches(workdir)
    assert "auoreo/stray" not in left and "auoreo/kdefault-1" not in left
    assert "auoreo/unmerged" in left and "auoreo/child-ab12" in left
    assert not p["merged"].exists() and p["running"].exists()
    journal = rc.journal_dir(services.settings.home) / rc.JOURNAL
    written = [json.loads(line) for line in journal.read_text().splitlines()]
    assert written == lines
    assert {(w.get("action"), w.get("reason")) for w in written} >= {
        ("remove", "merged"),
        ("prune", "missing"),
        ("delete_branch", "merged"),
        ("summary", None),
    }
    assert all("content" not in w for w in written)


async def test_server_pass_turns_the_journal_into_events_once(services: Services, workdir: Path) -> None:
    p = await _setup(services)
    top, items, findings = await _plan(services, {p["running"]})
    await rc.apply(
        top, items, findings, services.settings.home, refs={str(p["merged"].resolve()): "kanban:d#1"}
    )
    assert await rc.drain(services) == 2
    removed = [(a, d) for _, a, d in await events(services, "worktree.removed")]
    assert {d["reason"] for _, d in removed} == {"merged", "missing"} and all(
        a == "server" for a, _ in removed
    )
    assert next(d for _, d in removed if d["reason"] == "merged")["task_ref"] == "kanban:d#1"
    ((_, agg, summary),) = await events(services, "worktree.gc")
    assert agg == "server" and summary["removed"] == 1 and summary["pruned"] == 1
    assert summary["branches_deleted"] == 3 and summary["kept"] >= 2  # stray, kdefault-1 and kdefault-2
    assert await rc.drain(services) == 0 and len(await events(services, "worktree.gc")) == 1


async def test_drain_ignores_forged_or_broken_lines(services: Services) -> None:
    folder = rc.journal_dir(services.settings.home)
    folder.mkdir(parents=True)
    (folder / rc.JOURNAL).write_text(
        'not json\n{"action": "remove", "path": "/x", "reason": "because"}\n'
        '{"action": "remove", "path": "/y", "reason": "stale"}\n{"action": "summary", "kept": "lots"}\n'
    )
    assert await rc.drain(services) == 1
    ((_, _, data),) = await events(services, "worktree.removed")
    assert data == {"path": "/y", "reason": "stale"}


async def test_missing_worktree_and_branch_are_reported_and_restorable(
    services: Services, workdir: Path
) -> None:
    init_with_identity(workdir)
    flags_on(services)
    root = session_cwd.orch_root(services.settings, workdir)
    tree = await wt.task_worktree(workdir, root, "kdefault-5")
    sha = commit(tree.path, {"w.txt": "work\n"})
    kb = services.ext["teams"].kanban
    task = await kb.create("default", "t")
    await kb.attach(task.id, str(tree.path), note=worktree_spawn.attachment_note(tree))
    await services.emit("ses_w", "review.requested", {"branch": tree.branch, "sha": sha})
    git(workdir, "worktree", "remove", "--force", str(tree.path))
    git(workdir, "branch", "-D", tree.branch)  # a sibling deleted it
    _, items, findings = await _plan(services, set())
    got = {(f.kind, f.action) for f in findings if f.branch == tree.branch}
    assert got == {("missing_worktree", "report"), ("missing_branch", "report")}
    assert any(sha in f.reason for f in findings if f.kind == "missing_branch")
    top, items, findings = await _plan(services, set(), restore=True)
    await rc.apply(top, items, findings, services.settings.home)
    assert git(workdir, "rev-parse", tree.branch) == sha


async def test_flag_off_no_server_pass(services: Services) -> None:
    assert await rc.start(services) is None
    flags_on(services)
    services.settings.m7.orch.reconcile_interval_s = 0
    assert await rc.start(services) is None
    services.settings.m7.orch.reconcile_interval_s = 300
    started = await rc.start(services)
    assert started is not None
    await started.close()


def test_cli_reconcile_writes_the_journal_and_no_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auoreo.cli.commands import worktree as cli
    from auoreo.core import event_store

    repo = tmp_path / "repo"
    repo.mkdir()
    init_with_identity(repo)
    git(repo, "branch", "auoreo/stray")
    st = Settings(home=tmp_path / "home", gateway="fake")
    st.m7.orch.session_cwd = st.m7.orch.worktrees = True
    monkeypatch.setattr(cli, "settings", lambda: st)
    monkeypatch.chdir(repo)

    def no_events(*a: object, **k: object) -> None:
        raise AssertionError("the CLI must not write events")

    monkeypatch.setattr(event_store.EventStore, "append", no_events)
    res = CliRunner().invoke(cli.app, ["--reconcile", "--yes"])  # one command: no group
    assert res.exit_code == 0, res.output
    assert "auoreo/stray" in res.output and "auoreo/stray" not in branches(repo)
    journal = rc.journal_dir(st.home) / rc.JOURNAL
    assert any(json.loads(line).get("branch") == "auoreo/stray" for line in journal.read_text().splitlines())
    st.m7.orch.worktrees = False
    res = CliRunner().invoke(cli.app, ["--reconcile", "--yes"])  # one command: no group
    assert res.exit_code == 1 and "m7.orch.worktrees" in res.output
