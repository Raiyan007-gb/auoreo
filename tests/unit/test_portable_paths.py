"""Every tracked path must check out on Windows, macOS and Linux.

Windows refuses reserved device names even with an extension (`aux.py`, `con.txt`), the
characters `<>:"|?*`, and names ending in a dot or space; it and macOS are case-insensitive, so
two paths that differ only by case collide; Windows checkouts usually lack symlink support (the
link becomes a text file holding the target); and deep paths hit MAX_PATH (260) once nested under
a user's checkout directory.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(\..*)?$", re.IGNORECASE)
BAD_CHARS = re.compile(r'[<>:"|?*\\\x00-\x1f]')
MAX_LEN = 200  # leaves ~60 chars for the checkout directory under MAX_PATH


def _git(*args: str) -> list[str]:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


@pytest.fixture(scope="module")
def paths() -> list[str]:
    return _git("ls-files", "-z")


def test_no_reserved_or_invalid_names(paths: list[str]) -> None:
    bad = [
        p
        for p in paths
        for part in p.split("/")
        if RESERVED.match(part) or BAD_CHARS.search(part) or part.endswith((".", " "))
    ]
    assert not bad, f"not valid on Windows: {bad}"


def test_no_case_collisions(paths: list[str]) -> None:
    seen: dict[str, set[str]] = defaultdict(set)
    for p in paths:
        parts = p.split("/")
        for i in range(1, len(parts) + 1):
            prefix = "/".join(parts[:i])
            seen[prefix.lower()].add(prefix)
    clashes = sorted(" | ".join(sorted(v)) for v in seen.values() if len(v) > 1)
    assert not clashes, f"paths that differ only by case: {clashes}"


def test_no_symlinks() -> None:
    entries = _git("ls-files", "-s", "-z")
    links = [e.split("\t", 1)[1] for e in entries if e.startswith("120000 ")]
    assert not links, f"symlinks break Windows checkouts, use a real file instead: {links}"


def test_paths_fit_windows_max_path(paths: list[str]) -> None:
    long_paths = [p for p in paths if len(p) > MAX_LEN]
    assert not long_paths, f"paths longer than {MAX_LEN} chars: {long_paths}"
