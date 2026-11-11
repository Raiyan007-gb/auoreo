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
import re
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


def private_objects(admin: Path) -> Path:
    """The worker's own object store: its commits land here, the shared one is read-only."""
    return admin / "objects"


def tree_env(common: Path, admin: Path, worktree: Path) -> dict[str, str]:
    """The worker's view of its worktree, from the server's records (its private objects
    first, the shared store as an alternate)."""
    env = {"GIT_DIR": str(admin), "GIT_COMMON_DIR": str(common), "GIT_WORK_TREE": str(worktree)}
    if private_objects(admin).is_dir():
        env["GIT_OBJECT_DIRECTORY"] = str(private_objects(admin))
        env["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = str(common / "objects")
    return env


async def resolve(server_cwd: Path, worktree: Path) -> tuple[Path, Path]:
    """`(common, admin)` of a registered worktree, else `WorktreeError`. Also refused when the
    repository enables `extensions.worktreeConfig` and a worker shell has had write access to
    the admin dir (its private object store exists): git would then read `config.worktree`,
    which the worker can write (filter drivers run on `git status`)."""
    common = await common_dir(server_cwd)
    admin = admin_dir(common, worktree) if common else None
    if common is None or admin is None:
        raise WorktreeError(f"{worktree} is not a registered worktree of {server_cwd}")
    if not private_objects(admin).is_dir():
        return common, admin  # no worker shell ever had write access to the admin dir
    try:
        flag = (await _git("config", "--bool", "extensions.worktreeConfig", cwd=server_cwd)).decode()
    except WorktreeError:
        flag = ""  # unset
    if flag.strip() == "true":
        raise WorktreeError("extensions.worktreeConfig is on: a worker's config.worktree would apply")
    return common, admin


async def tree_git(server_cwd: Path, worktree: Path, *args: str) -> str:
    """`git <args>` on `worktree` without discovering anything inside it. Raises
    `WorktreeError` when the server repository has no record of `worktree`."""
    common, admin = await resolve(server_cwd, worktree)
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


# ------------------------------------------------------------------ worker objects → shared store
_HEX = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")


async def _quiet(*args: str, cwd: Path, env: dict[str, str]) -> str | None:
    try:
        return (await _git(*HARDEN, *args, cwd=cwd, env=env)).decode().strip()
    except (WorktreeError, OSError):
        return None


async def _tip(server_cwd: Path, admin: Path, both: dict[str, str]) -> str | None:
    """The commit the worktree's HEAD names (read from the server's admin record)."""
    head = first_line(admin / "HEAD")
    ref = head.removeprefix("ref: ") if head.startswith("ref: refs/") else head
    if ref != head or _HEX.fullmatch(ref):
        return await _quiet("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", cwd=server_cwd, env=both)
    return None


async def import_objects(server_cwd: Path, worktree: Path) -> bool:
    """Copy the objects of the worktree's HEAD from the worker's private store into the shared
    one, so the branch reads (gc, merge-base, a human's `git merge`) work from the parent.
    `pack-objects` reads the private store as an alternate; `index-pack` verifies every object
    hash before the pack lands in the shared store. True when nothing was missing or the import
    succeeded; False when it failed (callers then keep the worktree)."""
    common = await common_dir(server_cwd)
    admin = admin_dir(common, worktree) if common else None
    if common is None or admin is None or not private_objects(admin).is_dir():
        return True  # no worker store here (a CLI worktree, or no shell ever ran)
    private = private_objects(admin)
    local = {"GIT_DIR": str(common)}
    both = {**local, "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(private)}
    tip = await _tip(server_cwd, admin, both)
    if tip is None:
        return True  # an unborn or unreadable HEAD: nothing a parent read could need
    if await _quiet("cat-file", "-e", f"{tip}^{{commit}}", cwd=server_cwd, env=local) is not None:
        return True
    refs = await _quiet("for-each-ref", "--format=%(objectname) %(refname)", cwd=server_cwd, env=local)
    have = {
        line.split(" ", 1)[0]
        for line in (refs or "").splitlines()
        if not line.partition(" ")[2].startswith("refs/heads/auoreo/")
    }
    revs = "\n".join([tip, "--not", *sorted(have)]) + "\n"
    try:
        pack = await _git(*HARDEN, "pack-objects", "--revs", "--stdout", "-q", cwd=server_cwd, env=both,
                          stdin=revs.encode())  # fmt: skip
        await _git(*HARDEN, "index-pack", "--stdin", cwd=server_cwd, env=local, stdin=pack)
    except (WorktreeError, OSError):
        return False
    return await _quiet("cat-file", "-e", f"{tip}^{{commit}}", cwd=server_cwd, env=local) is not None
