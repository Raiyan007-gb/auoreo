"""M7 W4 Wave 2, phase 2: the git plumbing of `git_merge` (`runtime/merge.py`) on temporary
repositories: preflight, dry run, card facts, tamper checks and the merge itself."""

from __future__ import annotations

import os
import sys
import zlib
from pathlib import Path

import pytest

from auoreo.runtime import merge as mg
from tests.worktree_helpers import git, git_init

posix = pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell script / symlinks")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_init(repo)
    git(repo, "config", "user.name", "t")
    git(repo, "config", "user.email", "t@example.invalid")
    return repo


def _branch(repo: Path, name: str, files: dict[str, str], *, start: str = "main", msg: str = "work") -> str:
    """Commit `files` on `name` (created from `start`) without touching the main checkout."""
    if start != name:
        git(repo, "branch", "-f", name, start)
    tmp_index = repo / ".git" / f"idx-{name.replace('/', '-')}"
    env = {**os.environ, "GIT_INDEX_FILE": str(tmp_index)}
    import subprocess

    def run(*args: str, stdin: str | None = None) -> str:
        return subprocess.run(
            ["git", *args], cwd=repo, env=env, input=stdin, check=True, capture_output=True, text=True
        ).stdout.strip()

    run("read-tree", name)
    for path, body in files.items():
        oid = run("hash-object", "-w", "--stdin", stdin=body)
        run("update-index", "--add", "--cacheinfo", f"100644,{oid},{path}")
    tree = run("write-tree")
    parent = run("rev-parse", name)
    commit = run(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.invalid",
        "commit-tree",
        tree,
        "-p",
        parent,
        "-m",
        msg,
    )
    run("update-ref", f"refs/heads/{name}", commit)
    tmp_index.unlink(missing_ok=True)
    return commit


async def test_target_preflight(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    t = await mg.target(repo)
    assert t.branch == "main" and len(t.head) == 40 and t.head == git(repo, "rev-parse", "HEAD")
    (repo / "README.md").write_text("changed\n")
    with pytest.raises(mg.MergeRefused) as exc:
        await mg.target(repo)
    assert exc.value.reason == "dirty"
    git(repo, "checkout", "--quiet", "README.md")
    (repo / "untracked.txt").write_text("x")  # untracked files do not make it dirty
    await mg.target(repo)
    git(repo, "checkout", "--quiet", "--detach")
    with pytest.raises(mg.MergeRefused) as exc:
        await mg.target(repo)
    assert exc.value.reason == "detached"
    git(repo, "checkout", "--quiet", "main")
    (repo / ".git" / "MERGE_HEAD").write_text(git(repo, "rev-parse", "HEAD") + "\n")
    with pytest.raises(mg.MergeRefused) as exc:
        await mg.target(repo)
    assert exc.value.reason == "in_progress"


async def test_tip_ancestry_and_facts(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    sha = _branch(repo, "auoreo/a", {"src/a.py": "a = 1\nb = 2\n"}, msg="Add a")
    assert await mg.tip(repo, "auoreo/a") == sha
    assert await mg.tip(repo, "auoreo/missing") is None
    assert await mg.is_ancestor(repo, base, sha) and not await mg.is_ancestor(repo, sha, base)
    assert await mg.commits(repo, base, sha) == [sha]
    assert await mg.subject(repo, sha) == "Add a"
    changes = await mg.changes(repo, base, sha)
    assert [(c.path, c.status, c.added, c.deleted) for c in changes] == [("src/a.py", "A", 2, 0)]


async def test_dry_run_clean_and_conflicting_leaves_checkout_alone(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    clean = _branch(repo, "auoreo/clean", {"new.txt": "n\n"})
    out = await mg.dry_run(repo, head, clean)
    assert out.clean and len(out.tree) == 40 and out.conflicts == []
    bad = _branch(repo, "auoreo/bad", {"README.md": "theirs\n"})
    _branch(repo, "main", {"README.md": "ours\n"})  # move main itself
    git(repo, "reset", "--quiet", "--hard", "main")
    head = git(repo, "rev-parse", "HEAD")
    before = git(repo, "status", "--porcelain")
    out = await mg.dry_run(repo, head, bad)
    assert not out.clean and out.conflicts == ["README.md"]
    assert git(repo, "status", "--porcelain") == before and git(repo, "rev-parse", "HEAD") == head


async def test_shared_commits_with_sibling_branches(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    planted = _branch(repo, "auoreo/b", {"evil.txt": "x\n"})
    sha = _branch(repo, "auoreo/a", {"a.txt": "a\n"}, start="auoreo/b")  # a's history contains b's commit
    shared = await mg.shared_with(repo, head, sha, "auoreo/a")
    assert shared == {"auoreo/b": 1}
    assert planted in await mg.commits(repo, head, sha)


def _loose(repo: Path, oid: str) -> Path:
    return repo / ".git" / "objects" / oid[:2] / oid[2:]


async def test_rehash_catches_an_object_overwritten_on_disk(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    sha = _branch(repo, "auoreo/a", {"a.txt": "honest\n"})
    assert await mg.rehash(repo, head, sha, cap=100) >= 3  # commit, tree, blob
    blob = git(repo, "rev-parse", f"{sha}:a.txt")
    path = _loose(repo, blob)
    path.chmod(0o644)
    body = b"evil!!\n"  # same size keeps git's own size checks quiet
    path.write_bytes(zlib.compress(b"blob %d\0" % len(body) + body))
    with pytest.raises(mg.MergeRefused) as exc:
        await mg.rehash(repo, head, sha, cap=100)
    assert exc.value.reason == "object_corrupt"
    with pytest.raises(mg.MergeRefused) as exc:
        await mg.rehash(repo, head, sha, cap=2)
    assert exc.value.reason == "too_many_objects"


def test_definitions_and_portability_rules() -> None:
    paths = [
        ".auoreo/hooks/x.yaml",
        "sub/.auoreo/tools/t/handler.py",
        ".github/workflows/ci.yml",
        ".gitattributes",
        "docs/.gitattributes",
        ".gitmodules",
        "src/ok.py",
        ".github/CODEOWNERS",
    ]
    assert mg.definitions(paths) == paths[:6]
    assert mg.unportable_name("src/ok.py") is None
    for bad in ("con.txt", "a/NUL", "x/lpt1.md", "a:b", "q?.txt", "trail."):
        assert mg.unportable_name(bad) is not None, bad


@posix
async def test_symlink_and_case_clash_additions_are_found(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "--quiet", "-b", "auoreo/s")
    (repo / "link").symlink_to("/etc/passwd")
    (repo / "readme.md").write_text("clash\n")
    git(repo, "add", "link", "readme.md")
    git(repo, "commit", "--quiet", "-m", "s")
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "--quiet", "main")
    out = await mg.dry_run(repo, head, sha)
    problems = await mg.unportable(repo, out.tree, await mg.changes(repo, head, sha))
    assert any("link" in p and "symlink" in p for p in problems)
    assert any("readme.md" in p and "case" in p for p in problems)


@posix
async def test_merge_by_sha_no_ff_and_no_hook_runs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    sha = _branch(repo, "auoreo/a", {"a.txt": "a\n"})
    _branch(repo, "auoreo/a", {"later.txt": "moved\n"}, start="auoreo/a")  # the ref moves on
    marker = tmp_path / "HOOK_RAN"
    for hook in (
        "post-merge",
        "pre-merge-commit",
        "commit-msg",
        "prepare-commit-msg",
        "reference-transaction",
    ):
        h = repo / ".git" / "hooks" / hook
        h.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
        h.chmod(0o755)
    merged = await mg.merge(repo, sha, "Merge auoreo/a (x)\n\nAuoreo-Task: kanban:default#1")
    assert git(repo, "rev-parse", "HEAD") == merged
    assert git(repo, "rev-parse", f"{merged}^1") == head and git(repo, "rev-parse", f"{merged}^2") == sha
    assert (repo / "a.txt").is_file() and not (repo / "later.txt").exists()  # the sha, not the branch
    assert "Auoreo-Task: kanban:default#1" in git(repo, "log", "-1", "--format=%B")
    assert not marker.exists()


async def test_failed_merge_is_aborted(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    bad = _branch(repo, "auoreo/bad", {"README.md": "theirs\n"})
    (repo / "README.md").write_text("ours\n")
    git(repo, "commit", "--quiet", "-am", "ours")
    head = git(repo, "rev-parse", "HEAD")
    with pytest.raises(mg.MergeFailedError):
        await mg.merge(repo, bad, "Merge")
    assert git(repo, "rev-parse", "HEAD") == head and git(repo, "status", "--porcelain") == ""
    assert not (repo / ".git" / "MERGE_HEAD").exists()


async def test_git_version_floor() -> None:
    assert mg.version_ok("git version 2.43.0") and mg.version_ok("git version 2.38.1.windows.1")
    assert not mg.version_ok("git version 2.37.9") and not mg.version_ok("garbage")
