"""M7 W4 hardening: what a worker in its own worktree must not be able to reach.

Each test reproduces a proven escape (a patch outside the worktree, git run by the
orchestrator inside the worker-controlled folder, a planted branch, gc removing a live child,
hooks running the worker's files) and asserts it is closed for worktree sessions."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.gateway.fake import Scripted
from auoreo.harness.manifests import Rule
from auoreo.runtime import cwd as session_cwd
from auoreo.runtime import worktrees as wt
from auoreo.runtime.subtask import WorktreeError
from tests.teams_helpers import RoutedGateway, call_tool, tool_ctx, turn
from tests.worktree_helpers import flags_on, git, git_init


@pytest.fixture
def gateway() -> RoutedGateway:
    return RoutedGateway()


@pytest.fixture(autouse=True)
def _tmp_is_not_a_free_zone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The default rules allow `$TMP/*` as an external directory; pytest's checkouts live
    there, so point `$TMP` elsewhere to see the real outside-cwd decision."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))


async def _tree(svc: Services, slug: str = "job-1", base: str | None = None) -> wt.NewFeature:
    root = session_cwd.orch_root(svc.settings, svc.cwd)
    return await wt.task_worktree(svc.cwd, root, slug, **({"base": base} if base else {}))


def _allow(rt: Any, *permissions: str) -> None:
    rules = [Rule(permission=p, pattern="*", action="allow") for p in permissions]
    rt.engine = PermissionEngine([], session_rules=rules)


def _patches(workdir: Path) -> dict[str, str]:
    (workdir / "old.txt").write_text("a\n", encoding="utf-8")
    return {
        "add": f"*** Begin Patch\n*** Add File: {workdir / 'added.txt'}\n+x\n*** End Patch\n",
        "update": f"*** Begin Patch\n*** Update File: {workdir / 'old.txt'}\n@@\n-a\n+b\n*** End Patch\n",
        "move": (
            "*** Begin Patch\n*** Update File: mine.txt\n*** Move to: "
            f"{workdir / 'moved.txt'}\n@@\n-m\n+n\n*** End Patch\n"
        ),
    }


def _untouched(workdir: Path) -> None:
    assert not (workdir / "added.txt").exists() and not (workdir / "moved.txt").exists()
    assert (workdir / "old.txt").read_text(encoding="utf-8") == "a\n"


# ------------------------------------------------------------------ 1: apply_patch
async def test_apply_patch_outside_the_worktree_is_refused_non_interactively(
    services: Services, workdir: Path, gateway: RoutedGateway
) -> None:
    git_init(workdir)
    flags_on(services)
    tree = (await _tree(services)).path
    (tree / "mine.txt").write_text("m\n", encoding="utf-8")
    patches = _patches(workdir)
    row = await services.create_session(cwd=tree)
    _allow(await services.coordinator.runtime(row.id), "edit", "read")
    gateway.route(
        "PATCH",
        *(Scripted(tool_calls=[("apply_patch", {"patch": p})]) for p in patches.values()),
        Scripted(text="done"),
    )
    # three refusals in a row may trip the denial guard: the run then ends "blocked"
    assert (await turn(services, row.id, "PATCH the parent")).status in {"completed", "blocked"}
    events = await services.events.replay(0, frozenset({f"session:{row.id}"}), limit=100_000)
    done = [e.data for e in events if e.type == "tool.completed"]
    assert [d["is_error"] for d in done] == [True, True, True]
    _untouched(workdir)
    assert (tree / "mine.txt").read_text(encoding="utf-8") == "m\n"


async def test_apply_patch_outside_the_worktree_asks_interactively(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git_init(workdir)
    flags_on(services)
    tree = (await _tree(services)).path
    (tree / "mine.txt").write_text("m\n", encoding="utf-8")
    asked: list[tuple[str, str]] = []
    executor = services.coordinator.runner.executor
    real_ask = executor._ask

    async def ask(
        call: Any, tool: Any, args: Any, ctx: Any, engine: Any, perm: str, pattern: str, *a: Any
    ) -> Any:
        if perm != "external_directory":
            return await real_ask(call, tool, args, ctx, engine, perm, pattern, *a)
        asked.append((perm, pattern))
        return executor._err(call, "refused by the test"), None

    monkeypatch.setattr(executor, "_ask", ask)
    row = await services.create_session(cwd=tree)
    ctx = tool_ctx(services, row.id)
    ctx.cwd, ctx.interactive = tree, True
    ctx.services["engine"] = PermissionEngine([Rule(permission="edit", pattern="*", action="allow")])
    for kind, patch in _patches(workdir).items():
        asked.clear()
        _, is_error = await call_tool(services, ctx, "apply_patch", {"patch": patch})
        assert is_error and asked == [("external_directory", f"{workdir}/*")], kind
    _untouched(workdir)
    # inside the worktree nothing is asked
    inside = "*** Begin Patch\n*** Add File: ok.txt\n+y\n*** End Patch\n"
    asked.clear()
    assert (await call_tool(services, ctx, "apply_patch", {"patch": inside}))[1] is False
    assert asked == [] and (tree / "ok.txt").exists()


async def test_apply_patch_in_the_server_cwd_is_unchanged(services: Services, workdir: Path) -> None:
    """Not a worktree session: the patch is gated by `edit` only, as before."""
    git_init(workdir)
    outside = workdir.parent / "elsewhere"
    outside.mkdir()
    row = await services.create_session()
    ctx = tool_ctx(services, row.id)
    patch = f"*** Begin Patch\n*** Add File: {outside / 'n.txt'}\n+x\n*** End Patch\n"
    assert (await call_tool(services, ctx, "apply_patch", {"patch": patch}))[1] is False
    assert (outside / "n.txt").exists()


# ------------------------------------------------------------------ 2: orchestrator git
def _plant_repo(tree: Path, marker: Path, origin: str = "https://github.com/victim/other.git") -> None:
    """What a worker can do inside its own worktree: replace the `.git` pointer with a
    repository whose config runs a script on any index refresh and names another origin."""
    (tree / ".git").unlink()
    git(tree, "init", "--quiet")
    script = tree / "evil.sh"
    script.write_text(f"#!/bin/sh\ntouch '{marker}'\ncat >/dev/null\nprintf 'x\\0'\n", encoding="utf-8")
    script.chmod(0o755)
    git(tree, "config", "core.fsmonitor", str(script))
    git(tree, "remote", "add", "origin", origin)
    (tree / "sub").mkdir()  # a nested repository is a submodule candidate for `git status`
    git(tree / "sub", "init", "--quiet")
    git(tree / "sub", "config", "core.fsmonitor", str(script))


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell script as fsmonitor")
async def test_gc_and_discard_never_run_git_in_a_planted_worktree_repository(
    services: Services, workdir: Path, tmp_path: Path
) -> None:
    git_init(workdir)
    tree = await _tree(services)
    marker = tmp_path / "PWNED"
    _plant_repo(tree.path, marker)
    root = session_cwd.orch_root(services.settings, services.cwd)
    _, items = await wt.plan_gc(workdir, root, 7)
    assert [(i.path.name, i.action) for i in items] == [(tree.path.name, "keep")]
    assert not marker.exists()
    await wt.discard_worktree(workdir, tree)
    assert not marker.exists() and tree.path.exists()  # git cannot tell it is clean: left alone


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell script as fsmonitor")
async def test_the_github_home_repo_of_a_worktree_session_is_the_server_checkouts(
    services: Services, workdir: Path, tmp_path: Path
) -> None:
    from auoreo.tools.ghcli import home_repo

    git_init(workdir)
    git(workdir, "remote", "add", "origin", "https://github.com/me/mine.git")
    flags_on(services)
    tree = (await _tree(services)).path
    row = await services.create_session(cwd=tree)
    ctx = tool_ctx(services, row.id)
    ctx.cwd = tree
    assert await home_repo(ctx) == "me/mine"
    marker = tmp_path / "PWNED"
    _plant_repo(tree, marker)
    assert await home_repo(ctx) == "me/mine"  # not victim/other
    assert not marker.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell script as fsmonitor")
async def test_the_review_tool_in_a_worktree_never_runs_the_workers_git_config(
    services: Services, workdir: Path, tmp_path: Path
) -> None:
    git_init(workdir)
    flags_on(services)
    tree = (await _tree(services)).path
    row = await services.create_session(cwd=tree)
    ctx = tool_ctx(services, row.id)
    ctx.cwd = tree
    (tree / "README.md").write_text("changed\n", encoding="utf-8")
    text, is_error = await call_tool(services, ctx, "review", {})
    assert not is_error and "README.md" in text  # the honest worktree is reviewed as before
    marker = tmp_path / "PWNED"
    _plant_repo(tree, marker)
    await call_tool(services, ctx, "review", {})
    assert not marker.exists()


# ------------------------------------------------------------------ 3: shared object store
def _worker_git(tree: Path, *args: str) -> str:
    """Git as the worker's shell runs it (the env `git_access` gives `bash`)."""
    from auoreo.runtime import worktree_git as wg

    common = Path(git(tree, "rev-parse", "--git-common-dir")).resolve()
    admin = wg.admin_dir(common, tree)
    assert admin is not None
    env = {
        **os.environ,
        "GIT_OBJECT_DIRECTORY": str(wg.private_objects(admin)),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(common / "objects"),
    }
    cmd = ["git", "-c", "user.name=w", "-c", "user.email=w@example.invalid", *args]
    return subprocess.run(cmd, cwd=tree, env=env, check=True, capture_output=True, text=True).stdout


async def test_worker_commits_reach_the_parent_repository(services: Services, workdir: Path) -> None:
    """Commits land in the worker's private store; the orchestrator imports them, so the
    parent's own git (merge-base, log, a human's merge), gc and the review tool all work."""
    git_init(workdir)
    flags_on(services)
    tree = await _tree(services)
    ctx = tool_ctx(services, "ses_x")
    ctx.cwd = tree.path
    access = await session_cwd.git_access(ctx)
    assert access.env and (workdir / ".git" / "objects").resolve() in access.readable
    (tree.path / "work.txt").write_text("w\n", encoding="utf-8")
    _worker_git(tree.path, "add", "work.txt")
    _worker_git(tree.path, "commit", "--quiet", "-m", "worker")
    tip = _worker_git(tree.path, "rev-parse", "HEAD").strip()
    with pytest.raises(subprocess.CalledProcessError):
        git(workdir, "cat-file", "-e", tip)  # only in the private store so far
    root = session_cwd.orch_root(services.settings, services.cwd)
    _, items = await wt.plan_gc(workdir, root, 7)
    assert [i.action for i in items] == ["keep"]
    assert git(workdir, "merge-base", "--is-ancestor", "main", tree.branch) == ""
    assert git(workdir, "log", "-1", "--format=%s", tree.branch) == "worker"
    git(workdir, "fsck", "--no-dangling")
    git(workdir, "merge", "--quiet", "--ff-only", tree.branch)  # the human's merge-back
    assert (workdir / "work.txt").exists()
    # review in the worktree reads the worker's own history
    row = await services.create_session(cwd=tree.path)
    rctx = tool_ctx(services, row.id)
    rctx.cwd = tree.path
    (tree.path / "work.txt").write_text("w2\n", encoding="utf-8")
    text, is_error = await call_tool(services, rctx, "review", {})
    assert not is_error and "work.txt" in text


@pytest.mark.skipif(sys.platform == "win32", reason="needs bash")
async def test_the_bash_tool_imports_the_workers_commits(services: Services, workdir: Path) -> None:
    git_init(workdir)
    flags_on(services)
    tree = await _tree(services)
    row = await services.create_session(cwd=tree.path)
    ctx = tool_ctx(services, row.id)
    ctx.cwd = tree.path
    (tree.path / "b.txt").write_text("b\n", encoding="utf-8")
    cmd = "git add b.txt && git -c user.name=w -c user.email=w@example.invalid commit -q -m viabash"
    text, is_error = await call_tool(services, ctx, "bash", {"command": cmd})
    assert not is_error, text
    assert git(workdir, "log", "-1", "--format=%s", tree.branch) == "viabash"
    git(workdir, "fsck", "--no-dangling")


@pytest.mark.skipif(shutil.which("bwrap") is None or sys.platform != "linux", reason="needs bubblewrap")
async def test_a_sandboxed_worker_cannot_destroy_the_shared_object_store(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git_init(workdir)
    flags_on(services)
    bdef = services.harness.select_backend("sandbox", "bwrap")
    assert bdef is not None
    backend = bdef.load()(services.settings)
    if not (await backend.check()).ok:
        pytest.skip("bubblewrap cannot run here")
    monkeypatch.setitem(services._backends, "sandbox", backend)
    tree = await _tree(services)
    row = await services.create_session(cwd=tree.path)
    ctx = tool_ctx(services, row.id)
    ctx.cwd = tree.path
    objects = workdir / ".git" / "objects"
    (tree.path / "s.txt").write_text("s\n", encoding="utf-8")
    commit = "git add s.txt && git -c user.name=w -c user.email=w@example.invalid commit -q -m sandboxed"
    text, is_error = await call_tool(services, ctx, "bash", {"command": commit})
    assert not is_error, text
    attacks = [
        f"rm -rf {objects}/*",
        f"echo /nonexistent > {objects}/info/alternates",
        f"echo '[core]' >> {workdir}/.git/config",
    ]
    for attack in attacks:
        await call_tool(services, ctx, "bash", {"command": attack})
    assert not (objects / "info" / "alternates").exists()
    git(workdir, "fsck", "--no-dangling")
    assert git(workdir, "log", "-1", "--format=%s", tree.branch) == "sandboxed"
    assert git(workdir, "log", "-1", "--format=%s", "main") == "init"


# ------------------------------------------------------------------ 4: slugs never collide
async def test_long_or_normalized_names_get_their_own_worktree(services: Services, workdir: Path) -> None:
    git_init(workdir)
    long = "a" * 60
    assert wt.task_slug(f"{long}-ab12") != wt.task_slug(f"{long}-cd34")
    assert len(wt.task_slug(f"{long}-ab12")) <= wt.MAX_SLUG
    for one, other in (("ka_b-1", "ka/b-1"), ("ka-b-1", "ka/b-1"), ("Job", "job"), ("con", "t-con")):
        assert wt.task_slug(one) != wt.task_slug(other), (one, other)
    assert wt.task_slug("kdefault-5") == "kdefault-5"  # a name that is already a slug is kept
    a = await _tree(services, f"{long}-ab12")
    b = await _tree(services, f"{long}-cd34")
    assert a.path != b.path and a.branch != b.branch and not b.resumed


async def test_a_worktree_on_another_branch_is_not_resumed(services: Services, workdir: Path) -> None:
    git_init(workdir)
    tree = await _tree(services, "job")
    git(tree.path, "switch", "--quiet", "-c", "auoreo/someone-else")  # the worker moved its HEAD
    with pytest.raises(WorktreeError, match="refusing to resume"):
        await _tree(services, "job", base=tree.base)
    git(tree.path, "switch", "--quiet", "auoreo/job")
    assert (await _tree(services, "job", base=tree.base)).resumed


# ------------------------------------------------------------------ 5: planted sibling branches
def _plant(sibling: Path, branch: str, *, orphan: bool = False) -> str:
    """Worker A (namespace write access) creates or moves another task's branch."""
    git(sibling, "switch", "--quiet", *(["--orphan", branch] if orphan else ["-c", branch]))
    (sibling / "planted.txt").write_text("x\n", encoding="utf-8")
    git(sibling, "add", "planted.txt")
    git(sibling, "commit", "--quiet", "--no-verify", "-m", "planted")
    return git(sibling, "rev-parse", "HEAD")


async def test_an_unrecorded_or_unrelated_branch_is_never_resumed(services: Services, workdir: Path) -> None:
    git_init(workdir)
    sibling = await _tree(services, "a-1")
    _plant(sibling.path, "auoreo/kdefault-5")
    git(sibling.path, "switch", "--quiet", "auoreo/a-1")
    with pytest.raises(WorktreeError, match="no record"):
        await _tree(services, "kdefault-5")  # the task never made it: refused
    own = await _tree(services, "own-1")
    git(own.path, "switch", "--quiet", "--detach")
    git(workdir, "branch", "-D", "auoreo/own-1")
    _plant(sibling.path, "auoreo/own-1", orphan=True)  # moved to history unrelated to its base
    git(sibling.path, "switch", "--quiet", "auoreo/a-1")
    git(workdir, "worktree", "remove", "--force", str(own.path))
    with pytest.raises(WorktreeError, match="does not descend"):
        await _tree(services, "own-1", base=own.base)


async def test_the_dispatcher_refuses_a_planted_branch_and_resumes_its_own(
    services: Services, workdir: Path, gateway: RoutedGateway
) -> None:
    git_init(workdir)
    flags_on(services)
    cfg = services.settings.i3.kanban
    cfg.boards, cfg.agent = ["jobs"], "implementer"
    kb = services.ext["teams"].kanban
    from auoreo.teams.dispatcher import KanbanDispatcher

    task = await kb.create("jobs", "KPLANT do it")
    sibling = await _tree(services, "a-1")
    planted = _plant(sibling.path, f"auoreo/kjobs-{task.id}")
    git(sibling.path, "switch", "--quiet", "auoreo/a-1")
    gateway.route("KPLANT", Scripted(text="ok"))
    disp = KanbanDispatcher(services)
    assert await disp.tick() == []
    assert (await kb.get(task.id)).status == "ready" and disp.guarded(task.id) == "worktree failed"
    assert git(workdir, "rev-parse", f"auoreo/kjobs-{task.id}") == planted  # untouched, not run
    # once the stray branch is gone the task gets its own; its base is recorded in the event
    git(workdir, "branch", "-D", f"auoreo/kjobs-{task.id}")
    disp.guards.clear()
    assert await disp.tick() == [task.id]
    from auoreo.teams.worktree_spawn import recorded_base

    head = git(workdir, "rev-parse", "HEAD")
    assert await recorded_base(services, "jobs", task.id) == head
    for _ in range(400):
        if not disp.running:
            break
        await asyncio.sleep(0.05)


# ------------------------------------------------------------------ 6: gc and live children
def _age(repo: Path, tree: Path, hours: float = 3) -> None:
    """The base commit and the worktree folder look `hours` old (as for a long-running child)."""
    old = time.time() - hours * 3600
    stamp = f"{int(old)} +0000"
    env = {**os.environ, "GIT_COMMITTER_DATE": stamp, "GIT_AUTHOR_DATE": stamp}
    cmd = [
        "git",
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.invalid",
        "commit",
        "-q",
        "--amend",
        "--no-edit",
    ]
    subprocess.run(cmd, cwd=repo, env=env, check=True, capture_output=True)
    git(tree, "reset", "--quiet", "--hard", git(repo, "rev-parse", "HEAD"))
    os.utime(tree, (old, old))


async def test_gc_keeps_a_live_childs_worktree_and_ages_by_session_activity(
    services: Services, workdir: Path, gateway: RoutedGateway
) -> None:
    from auoreo.teams.worktree_gc import guard

    git_init(workdir)
    flags_on(services)
    tree = await _tree(services, "live-child")
    _age(workdir, tree.path)
    root = session_cwd.orch_root(services.settings, services.cwd)
    _, items = await wt.plan_gc(workdir, root, 7)
    assert [(i.action, i.reason) for i in items] == [("remove", "merged")]  # what gc did before

    parent = await services.create_session()
    gate = asyncio.Event()
    gateway.route("LIVE", Scripted(text="still working"), gate=gate)
    teams = services.ext["teams"]
    child = await teams.spawn(
        parent.id, agent="default", prompt="LIVE go", name="c", background=True, cwd=tree.path
    )
    for _ in range(100):  # the child's turn has started and waits on the gateway
        if gateway.requests_for("LIVE"):
            break
        await asyncio.sleep(0.02)
    g = await guard(services.db)
    assert tree.path.resolve() in {p.resolve() for p in g.protected}
    _, items = await wt.plan_gc(workdir, root, 7, protected=g.protected, activity=g.activity)
    assert [(i.action, i.reason) for i in items] == [("keep", "task claimed or running")]

    gate.set()
    await services.coordinator.wait(child.session_id, 20)
    for _ in range(100):
        g = await guard(services.db)
        if tree.path.resolve() not in {p.resolve() for p in g.protected}:
            break
        await asyncio.sleep(0.05)
    assert tree.path.resolve() not in {p.resolve() for p in g.protected}  # finished: not live
    os.utime(tree.path, (time.time() - 3 * 3600,) * 2)
    _, items = await wt.plan_gc(workdir, root, 7, protected=g.protected, activity=g.activity)
    assert [i.action for i in items] == ["keep"]  # its session was active minutes ago
    assert g.activity[tree.path.resolve()] > time.time() - 600
