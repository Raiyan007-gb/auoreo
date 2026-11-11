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

M7 W4 (`m7.orch.worktrees`): `task_worktree` is the orchestrator's entry (delegate_task, the
kanban dispatcher): no network, no `gh`, base = the server checkout's `HEAD`, never a branch
reset, one `git worktree add` at a time per repository, a cap and a Windows path-length guard.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import sys
import time
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from auoreo.runtime import worktree_git as wg
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
    check_pr: bool = True,
    reset: bool = True,
    harden: bool = False,
) -> NewFeature:
    if not SLUG_RE.fullmatch(slug):
        raise WorktreeError(f"invalid slug '{slug}' (lowercase [a-z0-9._-], '/'-separated)")
    top = await toplevel(cwd)
    root = root or top / ".auoreo" / "worktrees"
    dest, branch = root / slug.replace("/", "+"), BRANCH_PREFIX + slug
    if (dest / ".git").exists():
        return NewFeature(dest, branch, base or "", resumed=True, notes=["resumed existing worktree"])
    notes: list[str] = []
    if not check_pr:
        pass
    elif shutil.which("gh") is None:
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
    opts = wg.HARDEN if harden else ()  # orchestrator: a checkout of a worker's branch runs no hook
    if reset:
        await _git(*opts, "worktree", "add", "--quiet", "-B", branch, str(dest), base, cwd=top)
    elif await _ok("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", cwd=top):
        # never `-B`: an existing branch is checked out as it is (git refuses one that is
        # already checked out in another worktree), so a re-dispatched task keeps its commits
        await _git(*opts, "worktree", "add", "--quiet", str(dest), branch, cwd=top)
        return NewFeature(dest, branch, base, resumed=True, notes=[*notes, "resumed existing branch"])
    else:
        await _git(*opts, "worktree", "add", "--quiet", "-b", branch, str(dest), base, cwd=top)
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


def _age_s(path: Path, commit_ts: str | None, active: float = 0.0) -> float:
    """Seconds since the worktree was last touched: its folder, its HEAD commit and (M7) the
    newest event of a session that runs there."""
    newest = max([path.stat().st_mtime, float(commit_ts or 0), active])
    return time.time() - newest


async def plan_gc(
    cwd: Path,
    root: Path | None,
    stale_days: float,
    *,
    force: bool = False,
    protected: Collection[Path] = (),
    activity: Mapping[Path, float] | None = None,
) -> tuple[Path, list[GcItem]]:
    """`protected`: worktrees of claimed tasks or live sessions; they are kept whatever their
    state. `activity`: per worktree, the newest session event (epoch seconds)."""
    keep = {_resolved(p) for p in protected}
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
        if path in keep:
            items.append(GcItem(path, "task claimed or running", "keep"))
            continue
        if not await wg.import_objects(top, path):  # its commits may live only in its own store
            items.append(GcItem(path, "worker commits not yet in the repository (import failed)", "keep"))
            continue
        if not path.exists():
            items.append(GcItem(path, "folder missing", "prune"))
            continue
        # git never runs inside the worktree as a repository: the worker owns `<path>/.git`
        dirty = await wg.tree_ok(top, path, "status", "--porcelain", "--ignore-submodules=all")
        if dirty is None and not force:
            items.append(GcItem(path, "git status failed (not a clean, registered worktree)", "keep"))
            continue
        if dirty and not force:
            items.append(GcItem(path, f"{len(dirty.splitlines())} uncommitted change(s)", "keep"))
            continue
        head = e.get("HEAD", "")
        commit_ts = await _ok("log", "-1", "--format=%ct", head, cwd=top) if head else None
        age = _age_s(path, commit_ts, (activity or {}).get(path, 0.0))
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
        force = ["--force"] if registered_force else []
        await _git(*wg.HARDEN, "worktree", "remove", *force, str(i.path), cwd=top)  # its status check too


# ------------------------------------------------------------------ M7 W4: task worktrees
WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul", *(f"{d}{n}" for d in ("com", "lpt") for n in range(1, 10))}
)
MAX_SLUG = 48
MAX_WINDOWS_PATH = 200
_LOCKS: dict[str, asyncio.Lock] = {}


def _readable(slug: str) -> str:
    slug = re.sub(r"\.{2,}", ".", slug).strip(".-")
    while slug.endswith(".lock"):  # every one: `x.lock.lock` must not end up as `x.lock`
        slug = slug.removesuffix(".lock").strip(".-")
    return slug


def task_slug(raw: str) -> str:
    """A flat slug that is safe as a folder on every OS and as a git ref: lowercase
    `[a-z0-9._-]`, no path separators or `..`, no leading/trailing dot or dash, no `.lock`
    suffix, and never a Windows reserved device name (`con`, `nul`, `com1`…, with or without
    an extension). A name that already is such a slug is kept as it is; any other (changed by
    the rules above, or longer than `MAX_SLUG`) ends in `-<8 hex of sha256(raw)>`, so two
    names never share one worktree and branch (`a_b`/`a/b`, `x`/`X`, two long names)."""
    slug = _readable(re.sub(r"[^a-z0-9._-]+", "-", raw.lower()))
    if not slug:
        raise WorktreeError(f"'{raw}' has no characters usable in a worktree name")
    if slug.split(".", 1)[0] in WINDOWS_RESERVED:
        slug = f"t-{slug}"
    if slug == raw and len(slug) <= MAX_SLUG:
        return slug
    tag = hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()[:8]
    head = _readable(slug[: MAX_SLUG - len(tag) - 1])
    return f"{head}-{tag}" if head else f"t-{tag}"


def check_path_length(dest: Path, *, windows: bool | None = None) -> None:
    windows = sys.platform == "win32" if windows is None else windows
    if windows and len(str(dest)) >= MAX_WINDOWS_PATH:
        raise WorktreeError(
            f"worktree path is {len(str(dest))} characters; Windows needs it under {MAX_WINDOWS_PATH} "
            "(set m7.orch.worktree_root to a shorter folder)"
        )


async def _live(top: Path, root: Path) -> int:
    listing = await _ok("worktree", "list", "--porcelain", cwd=top) or ""
    base = _resolved(root)
    return sum(1 for e in _entries(listing) if base in _resolved(Path(e["worktree"])).parents)


async def task_worktree(
    cwd: Path, root: Path, slug: str, *, max_worktrees: int = 8, base: str | None = None
) -> NewFeature:
    """Create (or resume) the worktree of one task on branch `auoreo/<slug>`. `base` is the
    commit recorded when the task's worktree was first made (kanban attachment): an existing
    branch is resumed only when its tip descends from it; without a record it is refused
    (another worker can create any `auoreo/*` branch, so an unrecorded one is not trusted)."""
    slug = task_slug(slug)
    top = await toplevel(cwd)
    async with _LOCKS.setdefault(str(top), asyncio.Lock()):
        dest, branch = root / slug, BRANCH_PREFIX + slug
        if not (dest / ".git").exists():
            check_path_length(dest)
            if await _live(top, root) >= max_worktrees:
                raise WorktreeError(
                    f"{max_worktrees} worktrees are live under {root} (m7.orch.max_worktrees); "
                    "merge or remove some (`auoreo worktree gc`)"
                )
        head = await _ok("rev-parse", "--verify", "--quiet", "HEAD^{commit}", cwd=top)
        if not head:
            raise WorktreeError(f"{top} has no commit to branch from")
        if (dest / ".git").exists() or await _ok(
            "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", cwd=top
        ):
            await _check_resumable(top, dest, branch, base)
        out = await new_feature(
            top, slug, root, base="HEAD", fetch=False, check_pr=False, reset=False, harden=True
        )
        out.base = base if out.resumed and base else head
        if out.resumed and (on := await _checked_out(top, dest)) != f"refs/heads/{out.branch}":
            raise WorktreeError(
                f"{dest} is on {on or 'no branch'}, not {out.branch}; refusing to resume it "
                "(remove it with `git worktree remove` or pick another task name)"
            )
        return out


async def _check_resumable(top: Path, dest: Path, branch: str, base: str | None) -> None:
    if base is None:
        raise WorktreeError(
            f"{branch} already exists but this task has no record of creating it; refusing to resume "
            "it (another worker may have made it). Delete the branch or check its commits by hand"
        )
    if not re.fullmatch(r"[0-9a-f]{4,64}", base):
        raise WorktreeError(f"recorded base '{base[:80]}' of {branch} is not a commit id")
    await wg.import_objects(top, dest)  # a live worktree's commits may still be private
    tip = f"refs/heads/{branch}"
    if await _ok(*wg.HARDEN, "merge-base", "--is-ancestor", base, tip, cwd=top) is None:
        raise WorktreeError(
            f"{branch} does not descend from the task's recorded base {base[:12]}; refusing to resume it"
        )


async def _checked_out(top: Path, dest: Path) -> str | None:
    """The branch the server repository records for the worktree at `dest` (never read from
    `<dest>/.git`, which the worker owns)."""
    listing = await _ok("worktree", "list", "--porcelain", cwd=top) or ""
    want = _resolved(dest)
    return next((e.get("branch") for e in _entries(listing) if _resolved(Path(e["worktree"])) == want), None)


async def discard_worktree(cwd: Path, tree: NewFeature) -> None:
    """Undo a worktree created by a spawn that then failed. A resumed or dirty one is left
    alone; the branch goes only when it has no commit of its own."""
    if tree.resumed:
        return
    top = await toplevel(cwd)
    if await wg.tree_ok(top, tree.path, "status", "--porcelain", "--ignore-submodules=all") != "":
        return  # a child already wrote there (or git cannot tell): nothing is thrown away
    async with _LOCKS.setdefault(str(top), asyncio.Lock()):
        if not await wg.import_objects(top, tree.path):
            return
        await _ok(*wg.HARDEN, "worktree", "remove", "--force", str(tree.path), cwd=top)
        if await _ok("merge-base", "--is-ancestor", tree.branch, "HEAD", cwd=top) is not None:
            await _ok("branch", "-D", tree.branch, cwd=top)
