"""G6 review: `subtask: true` commands run as a background child session; `worktree: true`
puts it in an isolated git worktree carrying the working changes; babysit-pr/greploop ship."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest

from auoreo.core.services import Services
from auoreo.harness.discovery import Root
from auoreo.harness.manifests import CommandManifest
from auoreo.harness.registry import Harness
from auoreo.runtime.subtask import WorktreeError, create_worktree, remove_worktree, worktree_root
from tests.conftest import REPO


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "-b", "main", cwd=path)
    (path / "app.py").write_text("def f():\n    return 1\n")
    git("add", ".", cwd=path)
    git("commit", "-qm", "init", cwd=path)
    return path


async def events_of(services: Services, aggregate: str) -> list[Any]:
    return [e for e in await services.events.replay(0) if e.aggregate_id == aggregate]


# ---------------------------------------------------------------- worktree
async def test_worktree_carries_working_changes_and_isolates(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    (repo / "app.py").write_text("def f():\n    return 2  # changed\n")
    (repo / "new.py").write_text("x = 1\n")
    git("add", "new.py", cwd=repo)
    before = git("status", "--porcelain", cwd=repo)
    wt = await create_worktree(repo, worktree_root(None, repo), "review-abc")
    assert wt == repo / ".auoreo" / "worktrees" / "review-abc"
    assert "return 2" in (wt / "app.py").read_text() and (wt / "new.py").read_text() == "x = 1\n"
    (wt / "app.py").write_text("scratch edits stay in the worktree\n")
    assert "return 2" in (repo / "app.py").read_text()
    assert git("status", "--porcelain", cwd=repo) == before  # worktrees folder is git-ignored
    await remove_worktree(wt)
    assert not wt.exists()


async def test_worktree_errors(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(WorktreeError, match="not inside a git repository"):
        await create_worktree(plain, tmp_path / "wt", "review-1")
    with pytest.raises(WorktreeError, match="invalid worktree slug"):
        await create_worktree(plain, tmp_path / "wt", "../escape")


# -------------------------------------------------------------- manifests
def test_review_command_and_skills_ship() -> None:
    h = Harness([Root("bundled", REPO)])
    review = h.commands["review"].manifest
    assert review.subtask and review.worktree and review.agent == "reviewer"
    assert "{{worktree}}" in h.commands["review"].template
    assert "babysit_pr" in h.commands and {"babysit-pr", "greploop"} <= set(h.skills)
    with pytest.raises(ValueError):
        CommandManifest(name="x", description="d", agent="Bad Agent!")


# ------------------------------------------------------------- dispatching
async def test_review_spawns_background_reviewer_in_worktree(services: Services, workdir: Path) -> None:
    make_repo(workdir)
    (workdir / "app.py").write_text("def f():\n    return 3\n")
    sess = await services.create_session(title="t")
    assert await services.coordinator.submit(sess.id, "/review app.py") is None  # no parent turn
    evs = await events_of(services, sess.id)
    sub = next(e for e in evs if e.type == "command.subtask").data
    assert sub["command"] == "review" and sub["agent"] == "reviewer" and sub["task_id"]
    wt = Path(sub["worktree"])
    assert wt.parent == workdir / ".auoreo" / "worktrees" and "return 3" in (wt / "app.py").read_text()
    children = [s for s in await services.list_sessions() if s.parent_id == sess.id]
    assert len(children) == 1 and children[0].agent == "reviewer"
    await asyncio.wait_for(services.ext["teams"]._tasks[children[0].id], 10)
    hist = await services.coordinator.runner.load_history(children[0].id)
    first = next(m for m in hist if m["role"] == "user")["content"]
    assert f"worktree at `{wt}`" in first and "review: app.py" in first
    parent = await services.coordinator.runner.load_history(sess.id)
    assert all("Load the `code-review`" not in str(m.get("content")) for m in parent)


async def test_subtask_without_git_still_spawns(services: Services) -> None:
    sess = await services.create_session(title="t")
    assert await services.coordinator.submit(sess.id, "/review") is None
    evs = await events_of(services, sess.id)
    failed = next(e for e in evs if e.type == "command.subtask_failed").data
    assert failed["stage"] == "worktree" and "git" in failed["error"]
    assert next(e for e in evs if e.type == "command.subtask").data["worktree"] is None
    await services.coordinator.background_idle(10)


async def test_subtask_without_teams_expands_inline(services: Services) -> None:
    services.ext.pop("teams", None)
    sess = await services.create_session(title="t")
    run_id = await services.coordinator.submit(sess.id, "/review x.py")
    assert run_id is not None
    await services.coordinator.wait(sess.id, 10)
    started = [e for e in await events_of(services, sess.id) if e.type == "run.started"]
    assert started[-1].data["agent"] == "reviewer"
    hist = await services.coordinator.runner.load_history(sess.id)
    assert hist[0]["content"].startswith("Load the `code-review` skill and review: x.py")
