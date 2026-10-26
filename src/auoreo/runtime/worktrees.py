"""H4: feature worktrees (spec §25.15 "Worktree tool"): `auoreo new-feature <slug>` and
`auoreo worktree gc`.

new-feature: slug `^[a-z0-9._-]+(/[a-z0-9._-]+)*$`, flattened `/ → +` for the folder;
`git fetch` then `git worktree add -B auoreo/<slug> .auoreo/worktrees/<flat> <base>`.
Scope check first: an open PR already on `auoreo/<slug>` stops it (needs `gh`; skipped with a
note when `gh` is missing). An existing `<wt>/.git` pointer resumes instead (no fetch).

gc: worktrees registered under the worktree root that are clean and either merged into the
main checkout's HEAD (or `origin/HEAD`) or untouched for `stale_days` are removed; missing
folders are pruned; unregistered folders under the root are orphans. Dirty worktrees are
never removed without `force`.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from auoreo.runtime.subtask import WorktreeError, _git, _prepare_root

SLUG_RE = re.compile(r"^[a-z0-9._-]+(/[a-z0-9._-]+)*$")
BRANCH_PREFIX = "auoreo/"
MERGED_GRACE_S = 3600.0
"""A fresh worktree with no commits of its own looks merged; give it an hour."""


async def _ok(*args: str, cwd: Path) -> str | None:
    try:
        return (await _git(*args, cwd=cwd)).decode().strip()
    except (WorktreeError, OSError):
        return None


async def toplevel(cwd: Path) -> Path:
    top = await _ok("rev-parse", "--show-toplevel", cwd=cwd)
    if not top:
        raise WorktreeError(f"{cwd} is not inside a git repository")
    return Path(top)


async def _open_pr(top: Path, branch: str) -> str | None:
    """URL of an open PR whose head is `branch`; None when none (or `gh` cannot tell)."""
    proc = await asyncio.create_subprocess_exec(
        "gh", "pr", "list", "--head", branch, "--state", "open", "--json", "url",
        cwd=top, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    try:
        rows = json.loads(out or b"[]")
    except ValueError:
        return None
    return str(rows[0]["url"]) if rows else None


@dataclass
class NewFeature:
    path: Path
    branch: str
    base: str
    resumed: bool = False
    notes: list[str] = field(default_factory=list)


async def new_feature(
    cwd: Path,
    slug: str,
    root: Path | None = None,
    *,
    base: str | None = None,
    fetch: bool = True,
    force: bool = False,
) -> NewFeature:
    if not SLUG_RE.fullmatch(slug):
        raise WorktreeError(f"invalid slug '{slug}' (lowercase [a-z0-9._-], '/'-separated)")
    top = await toplevel(cwd)
    root = root or top / ".auoreo" / "worktrees"
    dest, branch = root / slug.replace("/", "+"), BRANCH_PREFIX + slug
    if (dest / ".git").exists():
        return NewFeature(dest, branch, base or "", resumed=True, notes=["resumed existing worktree"])
    notes: list[str] = []
    if shutil.which("gh") is None:
        notes.append("gh not found: skipped the open-PR scope check")
    elif (url := await _open_pr(top, branch)) and not force:
        raise WorktreeError(f"an open PR already uses {branch}: {url} (pass --force to continue anyway)")
    if dirty := await _ok("status", "--porcelain", cwd=top):
        notes.append(f"main checkout has {len(dirty.splitlines())} uncommitted change(s); they stay there")
    if fetch and await _ok("remote", cwd=top) and await _ok("fetch", "--quiet", cwd=top) is None:
        notes.append("git fetch failed; branching from local refs")
    if base is None:
        base = (
            "origin/HEAD" if await _ok("rev-parse", "--verify", "--quiet", "origin/HEAD", cwd=top) else "HEAD"
        )
    _prepare_root(root)
    await _git("worktree", "add", "--quiet", "-B", branch, str(dest), base, cwd=top)
    return NewFeature(dest, branch, base, notes=notes)


# ------------------------------------------------------------------ gc
@dataclass
class GcItem:
    path: Path
    reason: str
    action: str  # remove | prune | keep


def _entries(porcelain: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for block in porcelain.strip().split("\n\n"):
        entry: dict[str, str] = {}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            entry[key] = value
        if "worktree" in entry:
            out.append(entry)
    return out


async def _merged(top: Path, head: str) -> bool:
    for ref in ("HEAD", "origin/HEAD"):
        if await _ok("merge-base", "--is-ancestor", head, ref, cwd=top) is not None:
            return True
    return False


def _resolved(path: Path) -> Path:
    return path.resolve()


def _age_s(path: Path, commit_ts: str | None) -> float:
    newest = max([path.stat().st_mtime, float(commit_ts or 0)])
    return time.time() - newest


async def plan_gc(
    cwd: Path, root: Path | None, stale_days: float, *, force: bool = False
) -> tuple[Path, list[GcItem]]:
    top = await toplevel(cwd)
    root = (root or top / ".auoreo" / "worktrees").resolve()
    listing = await _ok("worktree", "list", "--porcelain", cwd=top) or ""
    items: list[GcItem] = []
    registered: set[Path] = set()
    for e in _entries(listing):
        path = _resolved(Path(e["worktree"]))
        if root not in path.parents:
            continue
        registered.add(path)
        if not path.exists():
            items.append(GcItem(path, "folder missing", "prune"))
            continue
        dirty = await _ok("status", "--porcelain", cwd=path)
        if dirty and not force:
            items.append(GcItem(path, f"{len(dirty.splitlines())} uncommitted change(s)", "keep"))
            continue
        age = _age_s(path, await _ok("log", "-1", "--format=%ct", "HEAD", cwd=path))
        head = e.get("HEAD", "")
        if age > MERGED_GRACE_S and head and await _merged(top, head):
            items.append(GcItem(path, "merged", "remove"))
            continue
        stale = age > stale_days * 86400
        reason = f"untouched {age / 86400:.1f}d" + ("" if stale else f" (< {stale_days:g}d)")
        items.append(GcItem(path, reason, "remove" if stale else "keep"))
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if child.is_dir() and child.resolve() not in registered:
                items.append(GcItem(child, "orphan folder (not a registered worktree)", "remove"))
    return top, items


async def apply_gc(top: Path, items: list[GcItem], registered_force: bool = False) -> None:
    if any(i.action == "prune" for i in items):
        await _git("worktree", "prune", cwd=top)
    for i in items:
        if i.action != "remove":
            continue
        if i.reason.startswith("orphan"):
            shutil.rmtree(i.path)
            continue
        await _git("worktree", "remove", *(["--force"] if registered_force else []), str(i.path), cwd=top)
