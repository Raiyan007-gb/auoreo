"""M7 W4 Wave 2: the git plumbing of `git_merge` (design `m7-orchestration.md`, Wave 2 notes).

Everything runs in the server checkout (never in a worker's worktree) with `worktree_git.HARDEN`
(no fsmonitor, hooks or submodule recursion), replace refs ignored and no external diff or
textconv program. The source branch is untrusted input: the merge names a commit, never a
branch, and the checks here give the approval card facts a worker cannot fake:

- `target`: the checkout is on a branch, clean (tracked files) and not mid-merge/rebase;
- `dry_run`: `git merge-tree --write-tree` (git 2.38+) decides clean vs conflicts without
  touching a ref, the index or a working file;
- `rehash`: every object new to the target is read through one `cat-file --batch` and hashed
  again here, since git does not re-check a loose object's content on read;
- `definitions` / `unportable`: paths that would make agent-written definitions trusted, and
  additions that break the repository's Windows path rules (symlinks, gitlinks, reserved
  names, case-only clashes);
- `merge`: `merge --no-ff --no-edit --no-verify <sha>`; on failure `merge --abort`.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from auoreo.runtime import worktree_git as wg
from auoreo.runtime.subtask import WorktreeError, _git, _git_rc
from auoreo.runtime.worktrees import WINDOWS_RESERVED

SHA_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
MIN_GIT = (2, 38)
ENV = {"GIT_NO_REPLACE_OBJECTS": "1", "GIT_MERGE_AUTOEDIT": "no"}
NO_PROGRAMS = ("--no-ext-diff", "--no-textconv")
IN_PROGRESS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG", "rebase-merge", "rebase-apply")
DEFINITION_DIRS = (".auoreo",)
DEFINITION_FILES = frozenset({".gitattributes", ".gitmodules"})
UNSAFE_MODES = {"120000": "symlink", "160000": "submodule (gitlink)"}
_BAD_CHARS = re.compile(r'[<>:"|?*\\\x00-\x1f]')


class MergeRefused(Exception):
    """A check failed: nothing was changed. `reason` is the `merge.denied` reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class MergeFailedError(Exception):
    """git failed after the dry run passed; the merge was aborted."""


async def _run(top: Path, *args: str, stdin: bytes | None = None) -> str:
    return (await _git(*wg.HARDEN, *args, cwd=top, env=ENV, stdin=stdin)).decode(errors="replace").strip()


async def _ok(top: Path, *args: str) -> str | None:
    try:
        return await _run(top, *args)
    except (WorktreeError, OSError):
        return None


def version_ok(text: str) -> bool:
    m = re.search(r"(\d+)\.(\d+)", text)
    return m is not None and (int(m.group(1)), int(m.group(2))) >= MIN_GIT


async def git_version_ok(top: Path) -> bool:
    return version_ok(await _ok(top, "--version") or "")


# ------------------------------------------------------------------ target
@dataclass
class Target:
    top: Path
    branch: str  # short name, e.g. `main`
    head: str  # full sha


async def toplevel(cwd: Path) -> Path:
    top = await _ok(cwd, "rev-parse", "--show-toplevel")
    if not top:
        raise MergeRefused("not_a_repository", f"{cwd} is not inside a git repository")
    return Path(top)


async def target(top: Path) -> Target:
    """The checked-out branch and its HEAD; refuses detached, mid-operation or dirty checkouts.
    Untracked files do not count: git itself refuses a merge that would overwrite one."""
    ref = await _ok(top, "symbolic-ref", "-q", "HEAD")
    if not ref or not ref.startswith("refs/heads/"):
        raise MergeRefused("detached", "the server checkout is not on a branch (detached HEAD)")
    head = await _ok(top, "rev-parse", "--verify", "HEAD^{commit}")
    if not head:
        raise MergeRefused("no_commit", "the server checkout has no commit")
    gitdir = Path(await _run(top, "rev-parse", "--absolute-git-dir"))
    if busy := [name for name in IN_PROGRESS if (gitdir / name).exists()]:
        raise MergeRefused(
            "in_progress", f"a git operation is in progress in the server checkout ({busy[0]})"
        )
    if await _run(top, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all"):
        raise MergeRefused("dirty", "the server checkout has uncommitted changes to tracked files")
    return Target(top, ref.removeprefix("refs/heads/"), head)


# ------------------------------------------------------------------ source facts
async def tip(top: Path, branch: str) -> str | None:
    return await _ok(top, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}") or None


async def is_ancestor(top: Path, older: str, newer: str) -> bool:
    code, _, _ = await _git_rc(*wg.HARDEN, "merge-base", "--is-ancestor", older, newer, cwd=top, env=ENV)
    return code == 0


async def commits(top: Path, head: str, sha: str) -> list[str]:
    out = await _run(top, "rev-list", sha, "--not", head)
    return out.split()


async def subject(top: Path, sha: str) -> str:
    return await _run(top, "log", "-1", "--format=%s", sha)


@dataclass
class Change:
    path: str
    status: str  # A M D T (no renames)
    old_mode: str
    new_mode: str
    added: int | None = None  # None for binary files
    deleted: int | None = None


async def changes(top: Path, head: str, sha: str) -> list[Change]:
    """What the merge brings in: `merge-base(head, sha)..sha`, without renames."""
    raw = await _run(top, "diff", "--raw", "-z", "--no-renames", *NO_PROGRAMS, f"{head}...{sha}")
    parts = raw.split("\0")
    out: list[Change] = []
    for meta, path in zip(parts[0::2], parts[1::2], strict=False):
        fields = meta.lstrip(":").split()
        if len(fields) == 5:
            out.append(Change(path, fields[4][:1], fields[0], fields[1]))
    num = await _run(top, "diff", "--numstat", "-z", "--no-renames", *NO_PROGRAMS, f"{head}...{sha}")
    stats: dict[str, tuple[int | None, int | None]] = {}
    for entry in num.split("\0"):
        cols = entry.split("\t", 2)
        if len(cols) == 3:
            stats[cols[2]] = (_int(cols[0]), _int(cols[1]))
    for c in out:
        c.added, c.deleted = stats.get(c.path, (None, None))
    return out


def _int(text: str) -> int | None:
    return int(text) if text.isdigit() else None


async def shared_with(top: Path, head: str, sha: str, branch: str) -> dict[str, int]:
    """Other `auoreo/*` branches whose history (not in the target) shares commits with `sha`'s."""
    mine = set(await commits(top, head, sha))
    refs = await _run(top, "for-each-ref", "--format=%(objectname) %(refname:short)", "refs/heads/auoreo/")
    out: dict[str, int] = {}
    for line in refs.splitlines():
        oid, _, name = line.partition(" ")
        if name == branch or not oid:
            continue
        try:
            theirs = set(await commits(top, head, oid))
        except WorktreeError:
            continue  # an unreadable sibling ref is not this merge's input
        if both := mine & theirs:
            out[name] = len(both)
    return out


# ------------------------------------------------------------------ dry run
@dataclass
class DryRun:
    clean: bool
    tree: str
    conflicts: list[str] = field(default_factory=list)


async def dry_run(top: Path, head: str, sha: str) -> DryRun:
    args = ("merge-tree", "--write-tree", "--name-only", "--no-messages", "-z", head, sha)
    code, out, err = await _git_rc(*wg.HARDEN, *args, cwd=top, env=ENV)
    if code not in (0, 1):
        raise MergeRefused("dry_run_failed", f"git merge-tree failed: {err.decode(errors='replace')[-300:]}")
    parts = out.decode(errors="replace").split("\0")
    tree, names = parts[0].strip(), []
    for name in parts[1:]:
        if not name:
            break
        names.append(name)
    return DryRun(code == 0, tree, names)


# ------------------------------------------------------------------ tamper checks
async def object_format(top: Path) -> str:
    return await _ok(top, "rev-parse", "--show-object-format") or "sha1"


async def rehash(top: Path, head: str, sha: str, cap: int) -> int:
    """Re-hash every object new to the target; returns the count. Refuses above `cap`."""
    listing = await _run(top, "rev-list", "--objects", sha, "--not", head)
    oids = [line.split(" ", 1)[0] for line in listing.splitlines() if line]
    if len(oids) > cap:
        raise MergeRefused(
            "too_many_objects",
            f"{len(oids)} objects are new to the target (m7.orch.merge_max_objects={cap}); merge it by hand",
        )
    if not oids:
        return 0
    algo = "sha256" if await object_format(top) == "sha256" else "sha1"
    stdin = ("\n".join(oids) + "\n").encode()
    data = await _git(*wg.HARDEN, "cat-file", "--batch", cwd=top, env=ENV, stdin=stdin)
    pos = 0
    for oid in oids:
        end = data.index(b"\n", pos)
        header = data[pos:end].decode(errors="replace").split()
        if len(header) != 3 or header[0] != oid:
            raise MergeRefused("object_corrupt", f"object {oid[:12]} is missing or unreadable")
        kind, size = header[1], int(header[2])
        body = data[end + 1 : end + 1 + size]
        pos = end + 1 + size + 1
        digest = hashlib.new(algo, f"{kind} {size}\0".encode() + body).hexdigest()
        if digest != oid:
            raise MergeRefused(
                "object_corrupt", f"object {oid[:12]} does not match its id (content replaced)"
            )
    return len(oids)


def definitions(paths: list[str]) -> list[str]:
    """Paths that become trusted definitions or repository behaviour once merged."""
    out = []
    for path in paths:
        parts = PurePosixPath(path).parts
        if (
            any(p in DEFINITION_DIRS for p in parts[:-1])
            or path.startswith(".github/workflows/")
            or (parts and parts[-1] in DEFINITION_FILES)
        ):
            out.append(path)
    return out


def unportable_name(path: str) -> str | None:
    """Why `path` cannot be checked out on Windows (the repository's path invariant), else None."""
    for part in PurePosixPath(path).parts:
        if _BAD_CHARS.search(part):
            return f"{path}: character not allowed on Windows"
        if part.endswith((".", " ")):
            return f"{path}: name ends with a dot or space"
        if part.split(".", 1)[0].lower() in WINDOWS_RESERVED:
            return f"{path}: reserved Windows device name"
    return None


async def unportable(top: Path, tree: str, items: list[Change]) -> list[str]:
    """Problems with what the merge adds: symlinks, gitlinks, unportable names, case-only clashes."""
    problems = [f"{c.path}: {UNSAFE_MODES[c.new_mode]}" for c in items if c.new_mode in UNSAFE_MODES]
    added = [c for c in items if c.status != "D"]
    problems += [why for c in added if (why := unportable_name(c.path)) is not None]
    names = (await _run(top, "ls-tree", "-r", "-z", "--name-only", tree)).split("\0") if tree else []
    seen: dict[str, list[str]] = {}
    for name in filter(None, names):
        seen.setdefault(name.lower(), []).append(name)
    for c in added:
        if len(seen.get(c.path.lower(), [])) > 1:
            problems.append(
                f"{c.path}: case-only clash with {', '.join(n for n in seen[c.path.lower()] if n != c.path)}"
            )
    return problems


# ------------------------------------------------------------------ merge
async def merge(top: Path, sha: str, message: str) -> str:
    """`git merge --no-ff --no-edit --no-verify -m <message> <sha>` (hooks off); returns the
    merge commit. On failure the merge is aborted and `MergeFailedError` raised."""
    before = await _run(top, "rev-parse", "HEAD")
    args = ("merge", "--no-ff", "--no-edit", "--no-verify", "--no-stat", "-m", message, sha)
    code, _, err = await _git_rc(*wg.HARDEN, *args, cwd=top, env=ENV)
    if code != 0:
        await _ok(top, "merge", "--abort")
        now = await _ok(top, "rev-parse", "HEAD")
        note = "" if now == before else f"; HEAD is now {now}, was {before}"
        raise MergeFailedError(f"git merge failed: {err.decode(errors='replace').strip()[-300:]}{note}")
    merged = await _run(top, "rev-parse", "HEAD")
    parents = (await _run(top, "rev-list", "--parents", "-n", "1", merged)).split()[1:]
    if parents != [before, sha]:
        raise MergeFailedError(f"unexpected merge result {merged[:12]} (parents {parents})")
    return merged
