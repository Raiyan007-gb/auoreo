"""M7 W4: orchestrator-side git for a worker's worktree, trusting nothing the worker controls.

A worker can rewrite everything inside its worktree, including `<worktree>/.git`: replaced by
`git init`, its `config` can set `core.fsmonitor=<script>`, and any git the server then runs
with `cwd=<worktree>` executes that script as the server user, outside every sandbox. So the
orchestrator never lets git discover a repository in a worktree:

- the admin dir (`<common>/worktrees/<name>`) comes from the server repository's own records
  (`admin_dir`), never from `<worktree>/.git`;
- `tree_git` runs `git` with `GIT_DIR=<admin>`, `GIT_COMMON_DIR=<common>` (so a rewritten
  `<admin>/commondir` is ignored too) and `GIT_WORK_TREE=<worktree>`, plus `HARDEN`: no
  fsmonitor, no hooks, no submodule recursion;
- repository-level calls (`worktree add/remove`, branch reads) run in the server checkout.
"""

from __future__ import annotations

import os
from pathlib import Path

from auoreo.runtime.subtask import WorktreeError, _git

HARDEN = (
    "-c", "core.fsmonitor=false",
    "-c", f"core.hooksPath={os.devnull}",
    "-c", "submodule.recurse=false",
    "-c", "diff.ignoreSubmodules=all",
    "-c", "core.untrackedCache=false",
)  # fmt: skip
"""Config that wins over any repository config: nothing the worker wrote runs."""


def _norm(path: Path | str) -> str:
    return os.path.normcase(os.path.realpath(path))


def first_line(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[0].strip()
    except (OSError, IndexError):
        return ""


def pointer(path: Path | str) -> str:
    """A `.git` pointer path with its folder resolved and its last part kept as written (the
    worker owns `<worktree>/.git` and could make it a symlink)."""
    head, tail = os.path.split(os.path.abspath(path))
    return os.path.join(_norm(head), os.path.normcase(tail))


async def common_dir(server_cwd: Path) -> Path | None:
    """The server repository's common git dir (`<repo>/.git`), resolved."""
    try:
        raw = (await _git("rev-parse", "--git-common-dir", cwd=server_cwd)).decode().strip()
    except (WorktreeError, OSError):
        return None
    return Path(_norm(os.path.join(server_cwd, raw))) if raw else None


def admin_dir(common: Path, worktree: Path) -> Path | None:
    """The one admin record (`<common>/worktrees/<name>`) whose `gitdir` names `worktree`; None
    when no record or more than one claims it. Read from the server repository only."""
    records, want = common / "worktrees", pointer(Path(worktree) / ".git")
    mine = [
        d
        for d in (sorted(records.iterdir()) if records.is_dir() else [])
        if _norm(d) == os.path.join(_norm(records), os.path.normcase(d.name))  # a real folder there
        and (line := first_line(d / "gitdir"))
        and pointer(os.path.join(d, line)) == want  # relative with `worktree.useRelativePaths`
    ]
    return mine[0] if len(mine) == 1 else None


def tree_env(common: Path, admin: Path, worktree: Path) -> dict[str, str]:
    return {"GIT_DIR": str(admin), "GIT_COMMON_DIR": str(common), "GIT_WORK_TREE": str(worktree)}


async def tree_git(server_cwd: Path, worktree: Path, *args: str) -> str:
    """`git <args>` on `worktree` without discovering anything inside it. Raises
    `WorktreeError` when the server repository has no record of `worktree`."""
    common = await common_dir(server_cwd)
    admin = admin_dir(common, worktree) if common else None
    if common is None or admin is None:
        raise WorktreeError(f"{worktree} is not a registered worktree of {server_cwd}")
    env = tree_env(common, admin, worktree)
    return (await _git(*HARDEN, *args, cwd=worktree, env=env)).decode().strip()


async def tree_ok(server_cwd: Path, worktree: Path, *args: str) -> str | None:
    try:
        return await tree_git(server_cwd, worktree, *args)
    except (WorktreeError, OSError):
        return None


async def repo_git(server_cwd: Path, *args: str) -> str:
    """`git <args>` in the server checkout with `HARDEN` (no fsmonitor, hooks or submodules)."""
    return (await _git(*HARDEN, *args, cwd=server_cwd)).decode().strip()
