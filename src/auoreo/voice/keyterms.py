"""Default STT keyterms (spec §13.2, §24.5 — the Claude keyterms port).

`default_keyterms(cwd)` = project basename + git branch words + recently modified file names,
capped at `max_terms` (50). Git is read from `.git/HEAD` directly (worktrees follow the
`gitdir:` file) — no subprocess. Recent files: a bounded walk (depth 4, 4000 entries) that
skips VCS/build/vendor folders and dotfiles, newest mtime first, stems split on `_`/`-`.
Results are cached per cwd for `CACHE_S`.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

CACHE_S = 60.0
MAX_DEPTH = 4
MAX_ENTRIES = 4000
SKIP_DIRS = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".auoreo", "target", ".tox"}
)
_SPLIT = re.compile(r"[/_.\-\s]+")
_cache: dict[tuple[str, int], tuple[float, list[str]]] = {}


def git_branch(cwd: Path) -> str | None:
    """Current branch name from `.git/HEAD` (None when detached or not a repo)."""
    for d in (cwd, *cwd.parents):
        git = d / ".git"
        if git.is_file():  # worktree / submodule: `gitdir: <path>`
            text = git.read_text("utf-8", "replace").strip()
            if not text.startswith("gitdir:"):
                return None
            gitdir = Path(text.split(":", 1)[1].strip())
            git = gitdir if gitdir.is_absolute() else (d / gitdir)
        if git.is_dir():
            try:
                head = (git / "HEAD").read_text("utf-8", "replace").strip()
            except OSError:
                return None
            if head.startswith("ref: refs/heads/"):
                return head[len("ref: refs/heads/") :]
            return None
    return None


def recent_files(cwd: Path, limit: int) -> list[Path]:
    seen: list[tuple[float, Path]] = []
    count = 0
    base_depth = len(cwd.parts)
    for root, dirs, files in os.walk(cwd):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        if len(Path(root).parts) - base_depth >= MAX_DEPTH:
            dirs[:] = []
        for name in files:
            count += 1
            if count > MAX_ENTRIES:
                break
            if name.startswith("."):
                continue
            p = Path(root, name)
            try:
                seen.append((p.stat().st_mtime, p))
            except OSError:
                continue
        if count > MAX_ENTRIES:
            break
    seen.sort(key=lambda t: t[0], reverse=True)
    return [p for _, p in seen[:limit]]


def _words(text: str) -> list[str]:
    return [w for w in _SPLIT.split(text) if len(w) > 1]


def default_keyterms(cwd: Path, max_terms: int = 50) -> list[str]:
    """Ordered, de-duplicated keyterms (≤ `max_terms`)."""
    if max_terms <= 0:
        return []
    key = (str(cwd), max_terms)
    hit = _cache.get(key)
    if hit is not None and time.monotonic() - hit[0] < CACHE_S:
        return list(hit[1])
    terms: list[str] = []

    def add(term: str) -> None:
        if term and term.lower() not in {t.lower() for t in terms} and len(terms) < max_terms:
            terms.append(term)

    add(cwd.name)
    branch = git_branch(cwd)
    if branch:
        for w in _words(branch):
            add(w)
    try:
        for p in recent_files(cwd, max_terms):
            add(p.stem)
    except OSError:
        pass
    _cache[key] = (time.monotonic(), terms)
    return list(terms)
