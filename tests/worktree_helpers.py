"""Test helpers for M7 W4: a temporary git checkout and the two orchestration flags."""

from __future__ import annotations

import subprocess
from pathlib import Path

from auoreo.core.services import Services


def git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def git_init(repo: Path) -> Path:
    """`repo` becomes a clean checkout with one commit on `main`."""
    git(repo, "init", "--quiet", "-b", "main")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "--quiet", "-m", "init")
    return repo


def flags_on(svc: Services, *, worktrees: bool = True) -> None:
    svc.settings.m7.orch.session_cwd = True
    svc.settings.m7.orch.worktrees = worktrees


def branches(repo: Path) -> list[str]:
    return git(repo, "branch", "--format=%(refname:short)").split()


def worktree_paths(repo: Path) -> list[Path]:
    lines = git(repo, "worktree", "list", "--porcelain").splitlines()
    return [Path(line.split(" ", 1)[1]).resolve() for line in lines if line.startswith("worktree ")]
