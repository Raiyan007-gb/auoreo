"""Workspace trust for project definition roots (`<cwd>/.auoreo/`).

A repository can ship a `.auoreo/` folder. Until the user trusts that workspace, only
*inert* definitions load from it (agents, skills, prompts, prompt families, commands,
workflows, variants — text the model reads). Everything that executes code or widens
permissions is skipped: hooks (shell commands), MCP servers (stdio commands), tool
handlers, backends (impl.py), plugins, Starlark policies, scheduled jobs, webhook routes
and SSH host files (which choose the address a stored key is offered to). Permission files
still load, but only their deny/ask rules and floors; ALLOW rules are dropped, and project
agents lose `allow` rules and permissive modes.

Trust is stored as realpaths in `<home>/trusted_workspaces.json` and granted with
`auoreo trust [path]`, `POST /api/workspace/trust` or the UI banner. The real process
environment may set `AUOREO_SECURITY_TRUST_ALL_WORKSPACES=1` (CI, containers, the test
suite); the `AUOREO_SECURITY` prefix is protected, so a project `.env` cannot set it.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

TRUST_FILE = "trusted_workspaces.json"
TRUST_ALL_ENV = "AUOREO_SECURITY_TRUST_ALL_WORKSPACES"
UNTRUSTED_SKIP = frozenset(
    {"hooks", "mcp", "tools", "backends", "plugins", "policies", "jobs", "webhooks", "hosts"}
)
"""Kinds never loaded from an untrusted project root."""
PERMISSIVE_MODES = frozenset({"yolo", "acceptEdits", "smart"})
SKIP_NOTE = "skipped: workspace not trusted (run `auoreo trust`)"


def _key(path: Path) -> str:
    return os.path.realpath(path)


def workspace_of(root_path: Path) -> Path:
    """`<cwd>/.auoreo` → `<cwd>` (the trusted unit is the workspace folder)."""
    return root_path.parent if root_path.name == ".auoreo" else root_path


def _load(home: Path) -> dict[str, Any]:
    try:
        data = json.loads((home / TRUST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"workspaces": {}}
    if not isinstance(data, dict) or not isinstance(data.get("workspaces"), dict):
        return {"workspaces": {}}
    return data


def trust_all() -> bool:
    return os.environ.get(TRUST_ALL_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def is_trusted(home: Path, workspace: Path) -> bool:
    if trust_all():
        return True
    return _key(workspace) in _load(home)["workspaces"]


def listed(home: Path) -> dict[str, Any]:
    return dict(_load(home)["workspaces"])


def _save(home: Path, data: dict[str, Any]) -> None:
    home.mkdir(parents=True, exist_ok=True)
    tmp = home / f".{TRUST_FILE}.tmp"
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(home / TRUST_FILE)


def trust(home: Path, workspace: Path, actor: str = "user") -> str:
    """Record `workspace` (its realpath) as trusted; returns the stored key."""
    data = _load(home)
    key = _key(workspace)
    data["workspaces"][key] = {"trusted_at": time.time(), "actor": actor}
    _save(home, data)
    return key


def untrust(home: Path, workspace: Path) -> bool:
    data = _load(home)
    gone = data["workspaces"].pop(_key(workspace), None) is not None
    if gone:
        _save(home, data)
    return gone


def skipped_in(workspace: Path) -> list[tuple[str, str]]:
    """(kind, name) of the active definitions `<workspace>/.auoreo` holds (skipped untrusted)."""
    from auoreo.harness.discovery import Root, iter_candidates

    root = Root("project", workspace / ".auoreo")
    return [(c.kind, c.name) for k in sorted(UNTRUSTED_SKIP) for c in iter_candidates([root], k)]


def strip_allow(rules: list[Any]) -> list[Any]:
    """Drop ALLOW rules (deny/ask still apply) from an untrusted layer."""
    return [r for r in rules if getattr(r, "action", None) != "allow"]


def sanitize_agent_manifest(manifest: Any) -> Any:
    """An untrusted project agent keeps its prompt but loses allow rules + permissive modes."""
    update: dict[str, Any] = {}
    rules = strip_allow(list(manifest.permissions))
    if len(rules) != len(manifest.permissions):
        update["permissions"] = rules
    if manifest.permission_mode in PERMISSIVE_MODES:
        update["permission_mode"] = None
    return manifest.model_copy(update=update) if update else manifest


__all__ = [
    "SKIP_NOTE",
    "TRUST_ALL_ENV",
    "TRUST_FILE",
    "UNTRUSTED_SKIP",
    "is_trusted",
    "listed",
    "skipped_in",
    "sanitize_agent_manifest",
    "strip_allow",
    "trust",
    "trust_all",
    "untrust",
    "workspace_of",
]
