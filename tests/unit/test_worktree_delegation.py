"""M7 W4.2: `delegate_task(worktree=true)` (`m7.orch.worktrees`): one git worktree on branch
`auoreo/<slug>` per child, on a temporary repository. Every row of the design's behaviour
table: flag off, not a git checkout, dirty parent, fork mode, spawn-failure rollback, the
all-or-nothing batch, an existing branch resumed (never reset), a concurrent batch."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from auoreo.core.services import Services
from auoreo.gateway.fake import Scripted
from auoreo.runtime import cwd as session_cwd
from auoreo.runtime import worktrees as wt
from auoreo.runtime.subtask import WorktreeError
from auoreo.schema.events import WorktreeCreated
from tests.teams_helpers import RoutedGateway, call_tool, first_user, new_parent, tool_ctx
from tests.worktree_helpers import branches, flags_on, git, git_init, worktree_paths


@pytest.fixture
def gateway() -> RoutedGateway:
    return RoutedGateway()


def _root(svc: Services) -> Path:
    return session_cwd.orch_root(svc.settings, svc.cwd)


def _trees(svc: Services) -> list[Path]:
    return [p for p in worktree_paths(svc.cwd) if p != svc.cwd.resolve()]


async def _delegate(svc: Services, parent: str, **args: Any) -> tuple[str, bool]:
    return await call_tool(svc, tool_ctx(svc, parent), "delegate_task", args)


async def _events(svc: Services, sid: str, type_: str) -> list[dict[str, Any]]:
    evs = await svc.events.replay(0, frozenset({f"session:{sid}"}), limit=100_000)
    return [e.data for e in evs if e.type == type_]


async def _children(svc: Services, parent: str) -> list[str]:
    return [d["child"] for d in await _events(svc, parent, "subagent.start")]


# ------------------------------------------------------------------ slugs and guards
def test_slugs_are_safe_on_every_os() -> None:
    assert wt.task_slug("../../etc/passwd") == "etc-passwd"
    assert wt.task_slug("A\\B/C:d*e") == "a-b-c-d-e"
    assert wt.task_slug("task_ab12-9f") == "task_ab12-9f"
    for reserved in ("CON", "nul", "com1", "LPT9", "aux.txt", "prn.tar.gz"):
        slug = wt.task_slug(reserved)
        assert slug.startswith("t-") and slug.split(".", 1)[0] not in wt.WINDOWS_RESERVED
    assert wt.task_slug("a..b") == "a.b" and wt.task_slug(".hidden.") == "hidden"
    assert wt.task_slug("x.lock") == "x" and len(wt.task_slug("z" * 300)) <= wt.MAX_SLUG
    assert wt.task_slug("x.lock.lock") == "x" and wt.task_slug("y.lock-.lock.") == "y"  # git refuses *.lock
    for slug in map(wt.task_slug, ("../x", "con", "a b", "K1-7", "é/ü\\n")):
        assert wt.SLUG_RE.fullmatch(slug) and "/" not in slug and not set(slug) & set('<>:"|?*\\')
    with pytest.raises(WorktreeError, match="no characters"):
        wt.task_slug("../..")


def test_windows_path_length_guard() -> None:
    long = Path("C:/") / ("d" * 190) / "slug-1234"
    with pytest.raises(WorktreeError, match="under 200"):
        wt.check_path_length(long, windows=True)
    wt.check_path_length(long, windows=False)
    wt.check_path_length(Path("C:/trees/slug-1234"), windows=True)


async def test_existing_branch_is_resumed_never_reset(services: Services, workdir: Path) -> None:
    git_init(workdir)
    root = _root(services)
    first = await wt.task_worktree(workdir, root, "k1-7")
    assert first.branch == "auoreo/k1-7" and not first.resumed and first.path == root / "k1-7"
    (first.path / "work.txt").write_text("done\n", encoding="utf-8")
    git(first.path, "add", "work.txt")
    git(first.path, "commit", "--quiet", "-m", "worker commit")
    tip = git(workdir, "rev-parse", "auoreo/k1-7")
    again = await wt.task_worktree(workdir, root, "k1-7")  # the folder is still there
    assert again.resumed and again.path == first.path
    git(workdir, "worktree", "remove", "--force", str(first.path))
    back = await wt.task_worktree(workdir, root, "k1-7")  # the folder is gone, the branch is not
    assert back.resumed and git(workdir, "rev-parse", "auoreo/k1-7") == tip
    assert (back.path / "work.txt").read_text(encoding="utf-8") == "done\n"
    with pytest.raises(WorktreeError):  # checked out elsewhere: fail, never steal or reset
        await wt.task_worktree(workdir, root.parent / "second-root", "k1-7")
    assert git(workdir, "rev-parse", "auoreo/k1-7") == tip


async def test_cap_and_concurrent_creation(services: Services, workdir: Path) -> None:
    git_init(workdir)
    root = _root(services)
    made = await asyncio.gather(*(wt.task_worktree(workdir, root, f"par-{i}") for i in range(4)))
    assert len({m.path for m in made}) == 4 and len(_trees(services)) == 4
    with pytest.raises(WorktreeError, match="max_worktrees"):
        await wt.task_worktree(workdir, root, "one-more", max_worktrees=4)
    assert (await wt.task_worktree(workdir, root, "par-0", max_worktrees=4)).resumed  # resume is free


# ------------------------------------------------------------------ behaviour table
async def test_flag_off_refuses_and_starts_no_child(services: Services, workdir: Path) -> None:
    git_init(workdir)
    parent = await new_parent(services)
    text, err = await _delegate(services, parent, goal="OFF do it", worktree=True)
    assert err and "worktree is disabled (m7.orch.worktrees)" in text
    assert await _children(services, parent) == [] and _trees(services) == []
    text, err = await _delegate(services, parent, goal="OFF plain", name="plain")  # unchanged
    assert not err and "<task-notification>" in text and _trees(services) == []


async def test_not_a_git_checkout_is_an_error_not_a_fallback(services: Services) -> None:
    flags_on(services)
    parent = await new_parent(services)
    text, err = await _delegate(services, parent, goal="NOGIT do it", worktree=True)
    assert err and "not inside a git repository" in text
    assert await _children(services, parent) == []


async def test_child_runs_in_its_own_worktree(
    services: Services, workdir: Path, gateway: RoutedGateway
) -> None:
    git_init(workdir)
    flags_on(services)
    (workdir / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")  # dirty parent: allowed
    parent = await new_parent(services)
    gateway.route("ISO", Scripted(text="isolated answer"))
    text, err = await _delegate(services, parent, goal="ISO add a test", name="iso", worktree=True)
    assert not err and "isolated answer" in text
    (tree,) = _trees(services)
    assert tree.parent == _root(services).resolve() and tree.name.startswith("iso-")
    branch = f"auoreo/{tree.name}"
    assert branch in branches(workdir) and git(tree, "branch", "--show-current") == branch
    assert not (tree / "dirty.txt").exists()  # uncommitted parent changes are not carried over
    assert "uncommitted change" in text and str(tree) in text and branch in text
    (child,) = await _children(services, parent)
    assert (await services.coordinator.runtime(child)).cwd == tree
    (created,) = await _events(services, child, "worktree.created")
    event = WorktreeCreated.model_validate(created)
    assert (Path(event.path), event.branch, event.resumed) == (tree, branch, False)
    assert event.base == git(workdir, "rev-parse", "--short", "HEAD")
    assert event.task_id is None and "task_id" not in created
    prompt = first_user(gateway.requests_for("ISO")[0])
    assert str(tree) in prompt and branch in prompt and "not carried over" in prompt


async def test_fork_mode_is_allowed_and_states_the_cwd(
    services: Services, workdir: Path, gateway: RoutedGateway
) -> None:
    git_init(workdir)
    flags_on(services)
    parent = await new_parent(services)
    gateway.route("FORKED", Scripted(text="fork answer"))
    text, err = await _delegate(
        services, parent, goal="FORKED continue", name="fk", mode="fork", worktree=True
    )
    assert not err and "fork answer" in text
    (tree,) = _trees(services)
    (child,) = await _children(services, parent)
    rt = await services.coordinator.runtime(child)
    assert rt.cwd == tree and str(tree) in str(rt.history[0]["content"])


async def test_spawn_failure_removes_the_new_worktree(services: Services, workdir: Path) -> None:
    git_init(workdir)
    flags_on(services)
    parent = await new_parent(services)
    before = branches(workdir)
    text, err = await _delegate(services, parent, goal="BAD x", agent="no_such_agent", worktree=True)
    assert err and "unknown agent" in text
    assert _trees(services) == [] and branches(workdir) == before
    assert not any(_root(services).glob("task_*"))


async def test_batch_is_one_worktree_per_task(
    services: Services, workdir: Path, gateway: RoutedGateway
) -> None:
    git_init(workdir)
    flags_on(services)
    parent = await new_parent(services)
    for i in range(3):
        gateway.route(f"WTB-{i}", Scripted(text=f"batch {i}"))
    tasks = [{"goal": f"WTB-{i} part", "name": f"p{i}"} for i in range(3)]
    tasks[2]["worktree"] = False  # a per-task value wins over the batch default
    text, err = await _delegate(services, parent, tasks=tasks, worktree=True)
    assert not err and "3 completed" in text
    trees = _trees(services)
    assert sorted(t.name.rsplit("-", 1)[0] for t in trees) == ["p0", "p1"]
    cwds = [(await services.coordinator.runtime(c)).cwd for c in await _children(services, parent)]
    assert sorted(str(c) for c in cwds if c) == sorted(str(t) for t in trees) and cwds.count(None) == 1


async def test_batch_is_all_or_nothing(services: Services, workdir: Path) -> None:
    git_init(workdir)
    flags_on(services)
    services.settings.m7.orch.max_worktrees = 2
    parent = await new_parent(services)
    before = branches(workdir)
    tasks = [{"goal": f"AON-{i}", "name": f"a{i}"} for i in range(3)]
    text, err = await _delegate(services, parent, tasks=tasks, worktree=True)
    assert err and "max_worktrees" in text
    assert await _children(services, parent) == []  # no child starts
    assert _trees(services) == [] and branches(workdir) == before  # those created are removed


async def test_schema_declares_the_flag_at_both_levels(services: Services) -> None:
    params = services.harness.tools["delegate_task"].manifest.parameters
    assert params["properties"]["worktree"] == {**params["properties"]["worktree"], "type": "boolean"}
    assert params["properties"]["worktree"].get("default") is False
    item = params["properties"]["tasks"]["items"]
    assert item["properties"]["worktree"]["type"] == "boolean"
    assert params["additionalProperties"] is False and item["additionalProperties"] is False
