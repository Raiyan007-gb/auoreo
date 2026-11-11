"""M7 W4: the per-session workspace root (`m7.orch.session_cwd`, design `m7-orchestration.md`).

A session's cwd is the `cwd` of its append-only `session.created` event (no schema change);
`SessionRuntime.cwd` holds it once `coordinator.runtime()` has validated it, and `cwd_of` is
the one resolver every consumer calls (`None` = the server cwd, which is also the flag-off
answer). A cwd is accepted only when it is the server cwd or a worktree registered in the
server repository under a configured worktree root; anything else fails closed (`CONFIG`),
never a silent run in the parent checkout.

Definitions and trust stay bound to the server workspace: a worktree's own `.auoreo/` is never
discovered and `harness/trust.py` keys trust by exact path, so a worktree inherits nothing.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from auoreo.core.db import EventRow

if TYPE_CHECKING:
    from auoreo.core.services import Services

MISSING = "worktree for this session is missing"
BRANCH_NS = "auoreo"


class SessionCwdError(ValueError):
    """The requested or stored session cwd is not an accepted workspace root."""


@dataclass(kw_only=True)
class SessionCwd:
    """The cwd fields of `SessionRuntime` (kept here so `runner.py` stays under its cap)."""

    cwd: Path | None = None  # None = the server cwd
    cwd_error: str | None = None  # the stored cwd failed validation: every run fails `CONFIG`


def enabled(settings: Any) -> bool:
    return bool(settings.m7.orch.session_cwd)


def cwd_of(rt: Any, services: Any) -> Path:
    """The workspace root of `rt`'s session: its own cwd, else the server cwd."""
    own: Path | None = getattr(rt, "cwd", None)
    return own or Path(services.cwd)


def plans_of(rt: Any, services: Any) -> str:
    return str(cwd_of(rt, services) / ".auoreo" / "plans")


def _norm(path: Path | str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _is_dir(path: str) -> bool:
    return os.path.isdir(path)


def _absolute(path: Path | str) -> Path:
    return Path(os.path.abspath(path))


def _existing(paths: list[Path]) -> list[Path]:
    return [p for p in paths if p.exists()]


def _under(path: str, root: str) -> bool:
    return path != root and path.startswith(root.rstrip("\\/") + os.sep)


def repo_top(cwd: Path) -> Path:
    """The checkout that holds `cwd` (the nearest folder with a `.git` entry), else `cwd`."""
    for p in (cwd, *cwd.parents):
        if (p / ".git").exists():
            return p
    return cwd


def orch_root(settings: Any, cwd: Path) -> Path:
    """Where orchestrator worktrees go: `m7.orch.worktree_root`, else `g6.review.worktree_root`,
    else `<home>/worktrees/<repo>-<hash8>/`: outside the checkout, so the parent's `glob`/`grep`
    do not walk the children and the parent cannot write into them without a prompt."""
    raw = settings.m7.orch.worktree_root or settings.g6.review.worktree_root
    if raw:
        return Path(raw).expanduser()
    top = repo_top(cwd.resolve())
    tag = hashlib.sha256(_norm(top).encode()).hexdigest()[:8]
    return Path(settings.home).resolve() / "worktrees" / f"{top.name[:40] or 'repo'}-{tag}"


def worktree_roots(settings: Any, cwd: Path) -> list[Path]:
    """Every root a session cwd may live under: the orchestrator root and the `/review` root."""
    from auoreo.runtime.subtask import worktree_root

    return [orch_root(settings, cwd), worktree_root(settings, cwd)]


async def registered(cwd: Path) -> set[str]:
    """Normalized paths of the worktrees registered in the repository that holds `cwd`."""
    from auoreo.runtime import worktrees as wt

    listing = await wt._ok("worktree", "list", "--porcelain", cwd=cwd) or ""
    return {_norm(e["worktree"]) for e in wt._entries(listing)}


async def validate_session_cwd(services: Services, cwd: Path | str) -> Path | None:
    """`None` for the server cwd, the path for an accepted worktree; else `SessionCwdError`."""
    path = _norm(cwd)
    if path == _norm(services.cwd):
        return None
    roots = [_norm(r) for r in worktree_roots(services.settings, services.cwd)]
    if not any(_under(path, r) for r in roots):
        raise SessionCwdError(f"{MISSING}: {cwd} is not under a worktree root")
    if not _is_dir(path):
        raise SessionCwdError(f"{MISSING}: {cwd} does not exist")
    if path not in await registered(services.cwd):
        raise SessionCwdError(f"{MISSING}: {cwd} is not a registered worktree of {services.cwd}")
    return _absolute(cwd)


async def for_new_session(services: Services, cwd: Path | str) -> Path:
    """The cwd a new session is born with (checked at spawn)."""
    if not enabled(services.settings):
        raise ValueError("a per-session cwd needs m7.orch.session_cwd")
    return await validate_session_cwd(services, cwd) or services.cwd


async def stored_cwd(services: Services, session_id: str) -> str | None:
    q = (
        select(EventRow)
        .where(EventRow.aggregate_id == session_id, EventRow.type == "session.created")
        .order_by(EventRow.aggregate_seq)
        .limit(1)
    )
    async with services.db.session() as s:
        row = (await s.execute(q)).scalars().first()
    if row is None:
        return None
    raw = services.db.cipher.decrypt(row.data).get("cwd")
    return raw if isinstance(raw, str) and raw else None


async def load(services: Services, rt: Any) -> None:
    """Resolve and validate `rt.cwd` once, when the coordinator builds the runtime."""
    if not enabled(services.settings):
        return
    raw = await stored_cwd(services, rt.session_id)
    if raw is None:
        return
    try:
        rt.cwd = await validate_session_cwd(services, raw)
    except (SessionCwdError, OSError) as exc:
        rt.cwd_error = str(exc) if isinstance(exc, SessionCwdError) else f"{MISSING}: {exc}"


# ------------------------------------------------------------------ sandbox git allowance
@dataclass(frozen=True)
class GitAccess:
    """What a worktree session's shell gets for git (engine-computed, never model-supplied)."""

    writable: tuple[Path, ...] = ()
    readable: tuple[Path, ...] = ()
    env: dict[str, str] = field(default_factory=dict)

    def mounts(self) -> list[Path]:
        return [*self.writable, *self.readable]


_GIT_ALLOW: dict[str, GitAccess] = {}


async def _git_access(cwd: Path, server_cwd: Path) -> GitAccess:
    """Read from the server repository's records only. Git is never run in the worktree and
    `<worktree>/.git` is never read: the worker can rewrite that file, and a pointer to the
    parent's git directory would otherwise make all of it writable.

    Writable: the worktree's admin dir (HEAD, index, its private object store `objects/`) and
    the `auoreo/` branch namespace with its ref log. Read-only: the shared object store, used
    as an alternate (`GIT_ALTERNATE_OBJECT_DIRECTORIES`), so a worker can neither delete the
    parent's objects nor rewrite `objects/info/alternates`; its new objects go to
    `GIT_OBJECT_DIRECTORY=<admin>/objects` and the orchestrator imports them into the shared
    store (`worktree_git.import_objects`, verified by `index-pack`)."""
    from auoreo.runtime import worktree_git as wg

    top = await wg.common_dir(server_cwd)
    admin = wg.admin_dir(top, cwd) if top else None
    if top is None or admin is None:  # unknown here, or two records claim this folder: no allowance
        return GitAccess()
    private, shared = wg.private_objects(admin), top / "objects"
    private.mkdir(exist_ok=True)
    writable = [admin]
    branch = wg.first_line(admin / "HEAD").removeprefix("ref: refs/heads/")
    if branch.startswith(BRANCH_NS + "/"):  # the worker's branch namespace; never refs/heads itself
        writable += [top / "refs" / "heads" / BRANCH_NS, top / "logs" / "refs" / "heads" / BRANCH_NS]
    if not shared.is_dir():
        return GitAccess()
    env = {"GIT_OBJECT_DIRECTORY": str(private), "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(shared)}
    return GitAccess(tuple(_existing(writable)), (shared,), env)


async def _git_paths(cwd: Path, server_cwd: Path) -> list[Path]:
    """Every mount of the allowance (writable and read-only)."""
    return (await _git_access(cwd, server_cwd)).mounts()


def worktree_session(ctx: Any) -> bool:
    """True when a tool call runs in a session whose cwd is not the server cwd (a worktree,
    `m7.orch.session_cwd` on). Flag off, or the server cwd: False."""
    services = ctx.services.get("services")
    return services is not None and enabled(services.settings) and _norm(ctx.cwd) != _norm(services.cwd)


async def git_access(ctx: Any) -> GitAccess:
    """The git allowance of a shell command in a worktree session: the admin dir and the
    `auoreo/` branch namespace writable (git replaces a ref through a lock file beside it, so
    the namespace directory, not the single ref file), the shared object store read-only, and
    the object-directory environment. The parent's working tree, `config`, `hooks`, objects
    and every other ref stay unwritable. Empty for the server cwd."""
    if not worktree_session(ctx):
        return GitAccess()
    services = ctx.services["services"]
    key = _norm(ctx.cwd)
    if key not in _GIT_ALLOW:
        _GIT_ALLOW[key] = await _git_access(ctx.cwd, services.cwd)
    return _GIT_ALLOW[key]


async def git_allow(ctx: Any) -> list[Path]:
    """Every path of `git_access` (`ExecSpec.git_allow`: exempt from the outside-cwd rule)."""
    return (await git_access(ctx)).mounts()
