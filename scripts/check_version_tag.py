"""Release guard: `auoreo.__version__` == the release tag (minus `v`) == pyproject's version.

    python scripts/check_version_tag.py v0.2.0        # or GITHUB_REF_NAME=v0.2.0
Exits 1 with a message on any mismatch (spec: version == tag == `auoreo --version`).
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def package_version(root: Path = ROOT) -> str:
    text = (root / "src" / "auoreo" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        raise SystemExit("cannot find __version__ in src/auoreo/__init__.py")
    return m.group(1)


def pyproject_version(root: Path = ROOT) -> str:
    return str(tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])


def check(tag: str, root: Path = ROOT) -> list[str]:
    """Problems (empty = ok)."""
    name = tag.removeprefix("refs/tags/")
    want = name.removeprefix("v")
    problems = [] if name.startswith("v") else [f"tag {tag!r} must look like v<version>"]
    found = (("auoreo.__version__", package_version(root)), ("pyproject", pyproject_version(root)))
    for label, have in found:
        if have != want:
            problems.append(f"{label} is {have!r} but the tag says {want!r}")
    return problems


def main(argv: list[str]) -> int:
    tag = argv[1] if len(argv) > 1 else os.environ.get("GITHUB_REF_NAME", "")
    if not tag:
        print("usage: check_version_tag.py <tag>  (or set GITHUB_REF_NAME)", file=sys.stderr)
        return 2
    problems = check(tag)
    for p in problems:
        print(f"version guard: {p}", file=sys.stderr)
    if not problems:
        print(f"version guard: {tag} == {package_version()}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
