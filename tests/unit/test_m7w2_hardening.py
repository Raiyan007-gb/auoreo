"""M7 W4 Wave 2 hardening: orchestrator git never runs shared hooks (`subtask._git`), and a
worktree session's `apply_patch` never executes a formatter the worker could have planted."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.runtime import cwd as session_cwd
from auoreo.runtime import worktrees as wt
from auoreo.runtime.subtask import _git, create_worktree
from auoreo.tools.context import ToolContext
from auoreo.tools.formatters import format_files
from tests.worktree_helpers import flags_on, git_init

ALLOW = [Rule(permission="*", pattern="*", action="allow")]
posix = pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell script")


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return git_init(path)


def _exe(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


# ------------------------------------------------------------------ hooks in `_git`
@posix
async def test_git_runner_never_runs_shared_hooks(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    marker = tmp_path / "HOOK_RAN"
    for name in ("post-checkout", "reference-transaction", "post-index-change"):
        _exe(repo / ".git" / "hooks" / name, f"touch '{marker}'")
    await create_worktree(repo, tmp_path / "trees", "review-1")  # `/review`: `worktree add --detach`
    await wt.new_feature(repo, "feat", tmp_path / "feat", fetch=False, check_pr=False)  # CLI path
    await _git("branch", "other", cwd=repo)
    assert not marker.exists()


@posix
async def test_git_runner_overrides_a_configured_hooks_path(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    marker = tmp_path / "HOOK_RAN"
    _exe(tmp_path / "planted" / "post-checkout", f"touch '{marker}'")
    await _git("config", "core.hooksPath", str(tmp_path / "planted"), cwd=repo)
    await create_worktree(repo, tmp_path / "trees", "review-2")
    assert not marker.exists()


async def test_git_runner_error_names_the_subcommand(tmp_path: Path) -> None:
    from auoreo.runtime.subtask import WorktreeError

    with pytest.raises(WorktreeError, match="git rev-parse failed"):
        await _git("rev-parse", "--verify", "no-such-ref", cwd=_repo(tmp_path / "r"))


# ------------------------------------------------------------------ formatters in a worktree
def _prettier(root: Path, marker: Path) -> Path:
    return _exe(root / "node_modules" / ".bin" / "prettier", f"touch '{marker}'")


@posix
async def test_format_files_skips_prettier_inside_an_untrusted_tree(tmp_path: Path) -> None:
    tree, marker = tmp_path / "tree", tmp_path / "PRETTIER_RAN"
    _prettier(tree, marker)
    js = tree / "a.js"
    js.write_text("let a = 1\n")
    report = await format_files([js], {"PATH": "/usr/bin:/bin"}, untrusted=tree)
    assert not marker.exists() and report.formatted == []
    assert any("prettier" in n and "worktree" in n for n in report.notes)
    await format_files([js], {"PATH": "/usr/bin:/bin"})  # a trusted tree: unchanged behaviour
    assert marker.exists()


@posix
async def test_format_files_skips_a_path_binary_that_lives_in_the_untrusted_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree, marker = tmp_path / "tree", tmp_path / "RUFF_RAN"
    fake = _exe(tree / "bin" / "ruff", f"touch '{marker}'")
    monkeypatch.setenv("PATH", f"{fake.parent}:/usr/bin:/bin")
    (tree / "ruff.toml").write_text("line-length = 100\n")
    py = tree / "m.py"
    py.write_text("x  =  1\n")
    report = await format_files([py], {"PATH": "/usr/bin:/bin"}, untrusted=tree)
    assert not marker.exists() and report.formatted == [] and report.notes


@posix
async def test_apply_patch_in_a_worktree_session_never_runs_a_planted_prettier(
    services: Services, workdir: Path, tmp_path: Path
) -> None:
    git_init(workdir)
    flags_on(services)
    tree = (await wt.task_worktree(workdir, session_cwd.orch_root(services.settings, workdir), "job-1")).path
    marker = tmp_path / "PRETTIER_RAN"
    _prettier(tree, marker)
    patch = "*** Begin Patch\n*** Add File: a.js\n+let a = 1\n*** End Patch\n"
    ctx = ToolContext(
        session_id="s1",
        cwd=tree,
        home=services.settings.home,
        harness=services.harness,
        services={"services": services, "engine": PermissionEngine(list(ALLOW))},
    )
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", "apply_patch", json.dumps({"patch": patch})),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine(ALLOW),
    )
    assert not out.result.is_error, out.result.content
    assert (tree / "a.js").is_file() and not marker.exists()
    assert "worktree" in out.result.content  # the skip is a visible note


@posix
async def test_apply_patch_in_the_server_cwd_still_runs_the_project_prettier(
    services: Services, workdir: Path, tmp_path: Path
) -> None:
    git_init(workdir)
    flags_on(services)
    marker = tmp_path / "PRETTIER_RAN"
    _prettier(workdir, marker)
    patch = "*** Begin Patch\n*** Add File: a.js\n+let a = 1\n*** End Patch\n"
    ctx = ToolContext(
        session_id="s1",
        cwd=workdir,
        home=services.settings.home,
        harness=services.harness,
        services={"services": services, "engine": PermissionEngine(list(ALLOW))},
    )
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", "apply_patch", json.dumps({"patch": patch})),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine(ALLOW),
    )
    assert not out.result.is_error, out.result.content
    assert marker.exists()
