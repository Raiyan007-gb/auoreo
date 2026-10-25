"""Walk definition roots and list candidate entries — no parsing, no imports.

A *root* is a directory that may contain any of the kind folders (`agents/`, `tools/`,
...). Roots are searched in precedence order; the first root that defines a name wins.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

# kind -> (layout, marker). "dir" = one folder per entry holding `marker`;
# "file" = one file per entry matching `marker` glob.
LAYOUT: dict[str, tuple[str, str]] = {
    "agents": ("dir", "AGENT.md"),
    "tools": ("dir", "tool.yaml"),
    "skills": ("dir", "SKILL.md"),
    "workflows": ("file", "*.yaml"),
    "permissions": ("file", "*.yaml"),
    "mcp": ("file", "*.yaml"),
    "prompts": ("file", "*.md"),
    "families": ("file", "*.md"),  # lives at prompts/families/
    "variants": ("file", "variants.yaml"),  # lives at prompts/variants.yaml
    "commands": ("file", "*.md"),  # slash commands: `/name args` in a chat message
    "backends": ("dir2", "backend.yaml"),  # backends/<kind>/<name>/backend.yaml
    "hooks": ("file", "*.yaml"),  # lifecycle hooks (M5)
    "jobs": ("file", "*.yaml"),  # scheduled jobs (M5)
    "webhooks": ("file", "*.yaml"),  # inbound webhook routes (M5)
    "plugins": ("dir", "plugin.yaml"),  # out-of-process plugins (M5)
}

_SUBPATH: dict[str, str] = {"families": "prompts/families", "variants": "prompts"}


@dataclass(frozen=True)
class Root:
    source: str  # "runtime" | "project" | "bundled" | "extra:<n>"
    path: Path


@dataclass(frozen=True)
class Candidate:
    kind: str
    name: str  # folder name or file stem
    path: Path  # the manifest file
    root: Root


def iter_candidates(roots: list[Root], kind: str) -> Iterator[Candidate]:
    """Yield candidates for `kind` across roots in precedence order."""
    layout, marker = LAYOUT[kind]
    for root in roots:
        base = root.path / _SUBPATH.get(kind, kind)
        if not base.is_dir():
            continue
        if layout == "dir":
            for entry in sorted(base.iterdir()):
                manifest = entry / marker
                if entry.is_dir() and not entry.name.startswith((".", "_")) and manifest.is_file():
                    yield Candidate(kind, entry.name, manifest, root)
        elif layout == "dir2":  # <base>/<group>/<name>/<marker>; candidate name = group/name
            for group in sorted(base.iterdir()):
                if not group.is_dir() or group.name.startswith((".", "_")):
                    continue
                for entry in sorted(group.iterdir()):
                    manifest = entry / marker
                    if entry.is_dir() and not entry.name.startswith((".", "_")) and manifest.is_file():
                        yield Candidate(kind, f"{group.name}/{entry.name}", manifest, root)
        else:
            for entry in sorted(base.glob(marker)):
                if entry.is_file() and not entry.name.startswith((".", "_")):
                    if kind in {"prompts", "commands"} and entry.name.upper() == "README.MD":
                        continue
                    yield Candidate(kind, entry.stem, entry, root)


def default_roots(home: Path, cwd: Path, bundled: Path, extra: list[Path] | None = None) -> list[Root]:
    """Precedence: runtime (`~/.auoreo`) > project (`<cwd>/.auoreo`) > bundled > extra."""
    roots = [
        Root("runtime", home),
        Root("project", cwd / ".auoreo"),
        Root("bundled", bundled),
    ]
    roots += [Root(f"extra:{i}", p) for i, p in enumerate(extra or [])]
    seen: set[Path] = set()
    unique: list[Root] = []
    for r in roots:
        key = r.path.resolve() if r.path.exists() else r.path
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique
