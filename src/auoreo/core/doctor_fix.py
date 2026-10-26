"""I5: `auoreo doctor --fix` — SAFE automatic repairs only, each one listed and confirmed.

The doctor itself never repairs anything (spec §20.3); `--fix` is an explicit, separate
step that plans a short list of reversible, local fixes and applies only the ones the user
confirms (or all of them with `--yes`):

- `mkdir`: create a missing runtime directory (`<home>`, `sessions/`, `cache/`, `logs/`,
  `memory/`, `skills/`) with owner-only permissions;
- `chmod`: tighten a secret/state file readable by group/others to `0600`;
- `worktree`: prune/remove a stale feature worktree under `.auoreo/worktrees` (merged,
  untouched for `i5.doctor.worktree_stale_days`, missing or orphaned; dirty ones are kept).

Nothing here deletes user data outside stale worktrees, edits settings or touches secrets.
"""

from __future__ import annotations

import asyncio
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from auoreo.core.settings import Settings

RUNTIME_DIRS = ("", "sessions", "cache", "logs", "memory", "skills")
PRIVATE_FILES = ("secrets.db", ".secrets.key", ".db.key", "state.db", "memory.db", "settings.json")


@dataclass
class Fix:
    kind: str  # mkdir | chmod | worktree
    target: str
    detail: str
    apply: Callable[[], None]
    """Synchronous: called outside any event loop, after the user confirmed."""

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "target": self.target, "detail": self.detail}


def _mkdir(path: Path) -> Callable[[], None]:
    def go() -> None:
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            path.chmod(0o700)

    return go


def _chmod(path: Path) -> Callable[[], None]:
    def go() -> None:
        path.chmod(0o600)

    return go


def dir_fixes(st: Settings) -> list[Fix]:
    out = []
    for sub in RUNTIME_DIRS:
        path = st.home / sub if sub else st.home
        if not path.is_dir():
            out.append(Fix("mkdir", str(path), "create missing runtime directory (0700)", _mkdir(path)))
    return out


def mode_fixes(st: Settings) -> list[Fix]:
    if os.name != "posix":
        return []
    out = []
    for name in PRIVATE_FILES:
        path = st.home / name
        if not path.is_file() or path.is_symlink():
            continue
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            out.append(Fix("chmod", str(path), f"mode {mode:o} → 600 (owner-only)", _chmod(path)))
    return out


def worktree_root(st: Settings) -> Path | None:
    raw = st.g6.review.worktree_root
    return Path(raw).expanduser() if raw else None


async def worktree_fixes(st: Settings, cwd: Path, root: Path | None = None) -> list[Fix]:
    from auoreo.runtime import worktrees as wt
    from auoreo.runtime.subtask import WorktreeError

    try:
        top, items = await wt.plan_gc(cwd, root, st.i5.doctor.worktree_stale_days)
    except WorktreeError:
        return []

    def remover(item: Any) -> Callable[[], None]:
        def go() -> None:
            asyncio.run(wt.apply_gc(top, [item]))

        return go

    return [
        Fix("worktree", str(i.path), f"{i.action} stale worktree ({i.reason})", remover(i))
        for i in items
        if i.action != "keep"
    ]


async def plan(st: Settings, cwd: Path | None = None) -> list[Fix]:
    root = worktree_root(st)
    return [*dir_fixes(st), *mode_fixes(st), *await worktree_fixes(st, cwd or Path.cwd(), root)]
