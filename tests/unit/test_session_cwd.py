"""M7 W4.1: a session can have its own workspace root (`m7.orch.session_cwd`).

The cwd comes from the session's `session.created` event (no schema change), is validated at
spawn and at runtime load (fail closed), and is what file tools, the plans directory, hooks,
compaction restore and the policy/render context resolve against. Flag off = the server cwd."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from auoreo.backends.sandbox import ExecSpec
from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.core.settings_m7 import M7Orch
from auoreo.gateway.fake import Scripted
from auoreo.harness.manifests import Rule
from auoreo.runtime import cwd as session_cwd
from auoreo.runtime import worktrees as wt
from auoreo.runtime.compaction_restore import restore_files, session_metadata
from auoreo.runtime.subtask import WorktreeError
from tests.conftest import write
from tests.teams_helpers import RoutedGateway, turn
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


async def _tree(svc: Services, slug: str = "job-1") -> Path:
    root = session_cwd.orch_root(svc.settings, svc.cwd)
    return (await wt.task_worktree(svc.cwd, root, slug)).path


async def _events(svc: Services, sid: str) -> list[Any]:
    return await svc.events.replay(0, frozenset({f"session:{sid}"}), limit=100_000)


def _real(path: Path | str) -> str:
    return os.path.realpath(path)


def _allow(rt: Any, *permissions: str) -> None:
    rules = [Rule(permission=p, pattern="*", action="allow") for p in permissions]
    rt.engine = PermissionEngine([], session_rules=rules)


# ------------------------------------------------------------------ flag off
async def test_flag_off_is_todays_behaviour(services: Services, workdir: Path) -> None:
    git_init(workdir)
    row = await services.create_session()
    rt = await services.coordinator.runtime(row.id)
    assert rt.cwd is None and session_cwd.cwd_of(rt, services) == services.cwd
    created = (await _events(services, row.id))[0]
    assert created.type == "session.created" and created.data["cwd"] == str(services.cwd)
    with pytest.raises(ValueError, match=r"m7\.orch\.session_cwd"):
        await services.create_session(cwd=workdir / "elsewhere")


async def test_flag_off_ignores_a_cwd_already_in_the_event(services: Services, workdir: Path) -> None:
    git_init(workdir)
    flags_on(services)
    tree = await _tree(services)
    row = await services.create_session(cwd=tree)
    services.settings.m7.orch.session_cwd = False  # rollback: the stored cwd is not honoured
    rt = await services.coordinator.runtime(row.id)
    assert rt.cwd is None and session_cwd.cwd_of(rt, services) == services.cwd


def test_worktrees_require_session_cwd() -> None:
    with pytest.raises(ValueError, match="session_cwd"):
        M7Orch(worktrees=True)
    orch = M7Orch(session_cwd=True, worktrees=True)
    assert orch.max_worktrees == 8 and orch.worktree_root is None


# ------------------------------------------------------------------ resolution
async def test_cwd_comes_from_the_session_created_event(services: Services, workdir: Path) -> None:
    git_init(workdir)
    flags_on(services)
    tree = await _tree(services)
    row = await services.create_session(cwd=tree)
    assert (await _events(services, row.id))[0].data["cwd"] == str(tree)
    rt = await services.coordinator.runtime(row.id)
    assert rt.cwd == tree and session_cwd.cwd_of(rt, services) == tree
    services.coordinator.forget(row.id)  # a restart: the runtime is rebuilt from the event
    assert (await services.coordinator.runtime(row.id)).cwd == tree
    plain = await services.create_session()
    assert (await services.coordinator.runtime(plain.id)).cwd is None  # the server cwd


async def test_orchestrator_worktrees_live_outside_the_checkout(services: Services, workdir: Path) -> None:
    git_init(workdir)
    root = session_cwd.orch_root(services.settings, services.cwd)
    assert services.settings.home.resolve() in root.parents and workdir not in root.parents
    services.settings.m7.orch.worktree_root = str(workdir.parent / "trees")
    assert session_cwd.orch_root(services.settings, services.cwd) == workdir.parent / "trees"


async def test_an_unregistered_cwd_is_refused_at_spawn(services: Services, workdir: Path) -> None:
    git_init(workdir)
    flags_on(services)
    root = session_cwd.orch_root(services.settings, services.cwd)
    stray = root / "not-a-worktree"
    stray.mkdir(parents=True)
    for bad in (stray, workdir.parent / "other", workdir / "sub"):
        with pytest.raises(session_cwd.SessionCwdError):
            await services.create_session(cwd=bad)
    # a registered worktree outside the configured roots is refused too
    outside = workdir.parent / "outside-root"
    git(workdir, "worktree", "add", "--quiet", "-b", "side", str(outside))
    with pytest.raises(session_cwd.SessionCwdError):
        await services.create_session(cwd=outside)


async def test_a_missing_worktree_fails_closed(
    services: Services, workdir: Path, gateway: RoutedGateway
) -> None:
    git_init(workdir)
    flags_on(services)
    tree = await _tree(services)
    row = await services.create_session(cwd=tree)
    services.coordinator.forget(row.id)
    git(workdir, "worktree", "remove", "--force", str(tree))
    gateway.route("GONE", Scripted(tool_calls=[("write", {"path": "leak.txt", "content": "x"})]))
    res = await turn(services, row.id, "GONE write a file")
    assert res.status == "failed" and res.error["code"] == "CONFIG"
    assert "worktree for this session is missing" in res.error["message"]
    assert not (workdir / "leak.txt").exists()  # never silently run in the parent checkout
    failed = [e for e in await _events(services, row.id) if e.type == "run.failed"]
    assert failed and failed[-1].data["code"] == "CONFIG"


# ------------------------------------------------------------------ file tools
async def test_file_tools_run_in_the_worktree_and_the_parent_is_outside(
    services: Services, workdir: Path, gateway: RoutedGateway
) -> None:
    git_init(workdir)
    flags_on(services)
    tree = await _tree(services)
    row = await services.create_session(cwd=tree)
    rt = await services.coordinator.runtime(row.id)
    _allow(rt, "edit", "read")
    target = workdir / "parent.txt"
    gateway.route(
        "INTREE",
        Scripted(tool_calls=[("write", {"path": "mine.txt", "content": "child"})]),
        Scripted(tool_calls=[("write", {"path": str(target), "content": "escape"})]),
        Scripted(text="done"),
    )
    res = await turn(services, row.id, "INTREE write two files")
    assert res.status == "completed"
    assert (tree / "mine.txt").read_text(encoding="utf-8") == "child"
    assert not (workdir / "mine.txt").exists() and not target.exists()
    done = [e.data for e in await _events(services, row.id) if e.type == "tool.completed"]
    assert [d["is_error"] for d in done] == [False, True]
    # non-interactive: refused; interactive: the outside-cwd approval is asked
    here = session_cwd.cwd_of(rt, services)
    assert rt.engine.context["cwd"] == str(tree)
    assert session_cwd.plans_of(rt, services) == str(tree / ".auoreo" / "plans")
    ask = rt.engine.external_directory(target, here, interactive=True)
    assert ask is not None and ask.action == "ask"
    assert rt.engine.external_directory(tree / "mine.txt", here) is None


async def test_bash_runs_in_the_worktree_with_a_git_allowance(
    services: Services, workdir: Path, gateway: RoutedGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shell commands are not path-gated; isolation is the sandbox's job. Under an enforced
    backend the spec is rooted at the worktree and only the worktree's own git metadata is
    writable outside it (the fake backend records the spec; nothing runs)."""
    git_init(workdir)
    flags_on(services)
    tree = await _tree(services)
    bdef = services.harness.select_backend("sandbox", "fake")
    assert bdef is not None
    fake = bdef.load()(services.settings)
    monkeypatch.setitem(services._backends, "sandbox", fake)
    row = await services.create_session(cwd=tree)
    _allow(await services.coordinator.runtime(row.id), "bash")
    gateway.route(
        "SHELL", Scripted(tool_calls=[("bash", {"command": "git commit -m x"})]), Scripted(text="k")
    )
    assert (await turn(services, row.id, "SHELL commit")).status == "completed"
    spec: ExecSpec = fake.calls[-1]
    common = workdir / ".git"
    assert spec.cwd == tree and spec.mode == "workspace-write"
    allowed = {p.resolve() for p in spec.git_allow}
    assert allowed and allowed == {p.resolve() for p in spec.writable}
    assert (common / "objects").resolve() in allowed
    assert (common / "worktrees" / tree.name).resolve() in allowed
    assert (common / "refs" / "heads" / "auoreo").resolve() in allowed
    for forbidden in (workdir, common, common / "config", common / "hooks", common / "refs" / "heads"):
        assert forbidden.resolve() not in allowed
    assert spec.outside_cwd() == []  # exempt: no danger-full-access needed for a commit
    stray = ExecSpec(argv=["true"], cwd=tree, writable=[common / "config"], git_allow=list(spec.git_allow))
    assert stray.outside_cwd() == [(common / "config").resolve()]
    # the parent session gets no allowance
    parent = await services.create_session()
    _allow(await services.coordinator.runtime(parent.id), "bash")
    gateway.route("PARENTSH", Scripted(tool_calls=[("bash", {"command": "true"})]), Scripted(text="k"))
    await turn(services, parent.id, "PARENTSH run")
    assert fake.calls[-1].cwd == services.cwd and fake.calls[-1].git_allow == []


@pytest.mark.skipif(sys.platform == "win32", reason="needs bash")
async def test_without_a_sandbox_the_shell_is_not_confined(
    services: Services, workdir: Path, gateway: RoutedGateway
) -> None:
    """Stated limit: `bash` can reach the parent checkout when no sandbox is enforced."""
    git_init(workdir)
    flags_on(services)
    tree = await _tree(services)
    row = await services.create_session(cwd=tree)
    _allow(await services.coordinator.runtime(row.id), "bash")
    cmd = f"pwd > where.txt && touch '{workdir}/shell-escape.txt'"
    gateway.route("NOSB", Scripted(tool_calls=[("bash", {"command": cmd})]), Scripted(text="k"))
    await turn(services, row.id, "NOSB run")
    assert _real((tree / "where.txt").read_text(encoding="utf-8").strip()) == _real(tree)
    assert (workdir / "shell-escape.txt").exists()


# ------------------------------------------------------------------ trust, hooks, restore
async def test_a_worktree_never_loads_definitions_or_inherits_trust(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auoreo.harness import trust

    git_init(workdir)
    flags_on(services)
    tree = await _tree(services)
    write(
        tree / ".auoreo" / "hooks" / "evil.yaml",
        "name: evil\nevent: PreToolUse\ncommand: [python, -c, 'print(1)']\n",
    )
    write(tree / ".auoreo" / "tools" / "evil" / "tool.yaml", "name: evil\ndescription: x\n")
    write(tree / ".auoreo" / "tools" / "evil" / "handler.py", "async def run(args, ctx):\n    return 'x'\n")
    services.harness.reload()
    await services.create_session(cwd=tree)
    assert "evil" not in services.harness.tools
    assert "evil" not in services.harness.hooks
    monkeypatch.delenv("AUOREO_SECURITY_TRUST_ALL_WORKSPACES")
    trust.trust(services.settings.home, workdir, actor="test")
    assert trust.is_trusted(services.settings.home, workdir)
    assert not trust.is_trusted(services.settings.home, tree)


async def test_hook_processes_use_the_session_cwd(services: Services, workdir: Path) -> None:
    git_init(workdir)
    flags_on(services)
    tree = await _tree(services)
    row = await services.create_session(cwd=tree)
    await services.coordinator.runtime(row.id)
    assert services.coordinator.cwd_for(row.id) == tree
    assert services.coordinator.cwd_for("ses_unknown") == services.cwd


async def test_compaction_restore_reads_from_the_session_cwd(services: Services, workdir: Path) -> None:
    git_init(workdir)
    flags_on(services)
    tree = await _tree(services)
    (tree / "notes.txt").write_text("from the worktree", encoding="utf-8")
    (workdir / "notes.txt").write_text("from the parent", encoding="utf-8")
    row = await services.create_session(cwd=tree)
    rt = await services.coordinator.runtime(row.id)
    fn = {"name": "read", "arguments": json.dumps({"path": "notes.txt"})}
    middle = [{"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": fn}]}]
    assert "from the worktree" in "".join(restore_files(services, middle, [], rt))
    assert "from the parent" in "".join(restore_files(services, middle, []))
    assert f"cwd: {tree}" in await session_metadata(services, rt)


async def test_task_worktree_needs_a_git_checkout(services: Services, workdir: Path) -> None:
    with pytest.raises(WorktreeError, match="not inside a git repository"):
        await wt.task_worktree(workdir, workdir.parent / "trees", "x")


# ------------------------------------------------------------------ children
async def test_a_child_runs_in_its_worktree_and_its_helpers_stay_there(
    services: Services, workdir: Path, gateway: RoutedGateway
) -> None:
    from sqlalchemy import select

    from auoreo.core.db import TeamMemberRow

    git_init(workdir)
    flags_on(services)
    tree = await _tree(services)
    parent = await services.create_session()
    teams = services.ext["teams"]
    gateway.route("CHILD-A", Scripted(text="a done"))
    gateway.route("CHILD-B", Scripted(text="b done"))
    child = await teams.spawn(
        parent.id, agent="default", prompt="CHILD-A go", name="a", background=False, cwd=tree
    )
    crt = await services.coordinator.runtime(child.session_id)
    assert crt.cwd == tree and crt.engine.plans_dir == str(tree / ".auoreo" / "plans")
    assert (await _events(services, child.session_id))[0].data["cwd"] == str(tree)
    worker = await services.create_session(cwd=tree)  # a session already in a worktree
    grand = await teams.spawn(worker.id, agent="default", prompt="CHILD-B go", name="b", background=False)
    assert (await services.coordinator.runtime(grand.session_id)).cwd == tree  # inherited
    plain = await teams.spawn(parent.id, agent="default", prompt="CHILD-B again", name="c", background=False)
    assert (await services.coordinator.runtime(plain.session_id)).cwd is None
    async with services.db.session() as s:
        rows = {r.name: r.cwd for r in (await s.execute(select(TeamMemberRow))).scalars()}
    assert rows == {"a": str(tree), "b": str(tree), "c": str(services.cwd)}


async def test_review_child_gets_the_worktree_as_its_cwd(services: Services, workdir: Path) -> None:
    git_init(workdir)
    parent = await services.create_session()

    async def review() -> tuple[Path, Any]:
        await services.coordinator.submit(parent.id, "/review README.md", interactive=True)
        evs = [e for e in await _events(services, parent.id) if e.type == "command.subtask"]
        return Path(evs[-1].data["worktree"]), await services.coordinator.runtime(evs[-1].data["task_id"])

    tree, rt = await review()  # flag off: today's prompt-only isolation
    assert rt.cwd is None
    await services.coordinator.wait(rt.session_id, 20)
    flags_on(services, worktrees=False)
    tree, rt = await review()
    assert rt.cwd == tree and tree.parent == workdir / ".auoreo" / "worktrees"
    await services.coordinator.wait(rt.session_id, 20)


async def test_forks_and_workflow_steps_keep_the_workspace_root(services: Services, workdir: Path) -> None:
    from auoreo.core.sessions_ops import fork_session

    git_init(workdir)
    plain = await services.create_session()
    assert (await services.coordinator.runtime((await fork_session(services, plain.id)).id)).cwd is None
    flags_on(services)
    tree = await _tree(services)
    row = await services.create_session(cwd=tree)
    fork = await fork_session(services, row.id)
    assert (await services.coordinator.runtime(fork.id)).cwd == tree
