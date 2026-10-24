"""The `Harness`: the merged, validated view of every definition root.

Loading is all-or-nothing per entry: an entry that fails validation is skipped and
recorded in `errors`. Errors in *bundled* definitions are fatal (the shipped harness
must always be valid); errors in overlays warn unless `strict_overlays` is set.
"""

from __future__ import annotations

import fnmatch
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auoreo.harness.discovery import Root, iter_candidates
from auoreo.harness.loader import (
    LOADERS,
    AgentDef,
    FamilyDef,
    Loaded,
    LoadError,
    PromptDef,
    SkillDef,
    ToolDef,
)
from auoreo.harness.manifests import AgentManifest

log = logging.getLogger(__name__)

# Kinds merged by name (first root wins). Permissions are layered, not merged.
NAMED_KINDS = ("tools", "agents", "skills", "workflows", "mcp", "prompts", "families")


class HarnessError(Exception):
    def __init__(self, errors: list[LoadError]) -> None:
        self.errors = errors
        super().__init__("invalid bundled definitions:\n" + "\n".join(map(str, errors)))


@dataclass(frozen=True)
class Shadowed:
    kind: str
    name: str
    winner: str
    loser: str


@dataclass
class Snapshot:
    """Immutable-by-convention result of one load pass."""

    tools: dict[str, ToolDef] = field(default_factory=dict)
    agents: dict[str, AgentDef] = field(default_factory=dict)
    skills: dict[str, SkillDef] = field(default_factory=dict)
    workflows: dict[str, Loaded] = field(default_factory=dict)
    mcp: dict[str, Loaded] = field(default_factory=dict)
    prompts: dict[str, PromptDef] = field(default_factory=dict)
    families: dict[str, FamilyDef] = field(default_factory=dict)
    permission_layers: list[Loaded] = field(default_factory=list)  # low → high precedence
    variants: Loaded | None = None
    errors: list[LoadError] = field(default_factory=list)
    shadowed: list[Shadowed] = field(default_factory=list)


def _load_snapshot(roots: list[Root], strict_overlays: bool) -> Snapshot:
    snap = Snapshot()
    fatal: list[LoadError] = []

    def record(err: LoadError, source: str) -> None:
        snap.errors.append(err)
        if source == "bundled" or strict_overlays:
            fatal.append(err)
        else:
            log.warning("skipping overlay definition: %s", err)

    for kind in NAMED_KINDS:
        target: dict[str, Any] = getattr(snap, kind)
        for cand in iter_candidates(roots, kind):
            try:
                item = LOADERS[kind](cand)
            except LoadError as err:
                record(err, cand.root.source)
                continue
            name = item.name if hasattr(item, "name") else cand.name
            if name in target:
                winner = target[name]
                snap.shadowed.append(Shadowed(kind, name, winner.source, cand.root.source))
                if kind == "tools" and not winner.manifest.override:
                    record(
                        LoadError(
                            winner.path / "tool.yaml",
                            f"tool '{name}' shadows a {cand.root.source} tool; set 'override: true' to allow",
                        ),
                        winner.source,
                    )
                    target[name] = item  # keep the lower-precedence original
                continue
            target[name] = item

    # Permission layers: every file loads; lowest precedence first (last match wins).
    for cand in reversed(list(iter_candidates(roots, "permissions"))):
        try:
            snap.permission_layers.append(LOADERS["permissions"](cand))
        except LoadError as err:
            record(err, cand.root.source)

    for cand in iter_candidates(roots, "variants"):
        try:
            snap.variants = LOADERS["variants"](cand)
        except LoadError as err:
            record(err, cand.root.source)
            continue
        break

    if fatal:
        raise HarnessError(fatal)
    return snap


class Harness:
    """Thread-safe holder of the current snapshot plus runtime-registered tools."""

    def __init__(self, roots: list[Root], strict_overlays: bool = False) -> None:
        self.roots = roots
        self.strict_overlays = strict_overlays
        self._lock = threading.RLock()
        self._snap = _load_snapshot(roots, strict_overlays)
        self._dynamic: dict[str, ToolDef] = {}  # e.g. MCP tools, keyed by name
        self.generation = 1

    # ------------------------------------------------------------------ views
    @property
    def snapshot(self) -> Snapshot:
        return self._snap

    @property
    def tools(self) -> dict[str, ToolDef]:
        with self._lock:
            merged = dict(self._dynamic)
            merged.update(self._snap.tools)  # builtins win on collision
            return merged

    @property
    def agents(self) -> dict[str, AgentDef]:
        return self._snap.agents

    @property
    def skills(self) -> dict[str, SkillDef]:
        return self._snap.skills

    @property
    def workflows(self) -> dict[str, Loaded]:
        return self._snap.workflows

    @property
    def errors(self) -> list[LoadError]:
        return self._snap.errors

    # -------------------------------------------------------------- mutation
    def reload(self) -> dict[str, Any]:
        """Re-discover everything. A failed reload keeps the previous snapshot."""
        new = _load_snapshot(self.roots, self.strict_overlays)
        with self._lock:
            old = self._snap
            self._snap = new
            self.generation += 1
        diff: dict[str, Any] = {
            "generation": self.generation,
            "errors": [str(e) for e in new.errors],
        }
        for kind in NAMED_KINDS:
            before, after = set(getattr(old, kind)), set(getattr(new, kind))
            diff[kind] = {"added": sorted(after - before), "removed": sorted(before - after)}
        return diff

    def register_dynamic(self, tool: ToolDef) -> None:
        with self._lock:
            self._dynamic[tool.name] = tool
            self.generation += 1

    def unregister_dynamic(self, prefix: str) -> None:
        with self._lock:
            for name in [n for n in self._dynamic if n.startswith(prefix)]:
                del self._dynamic[name]
            self.generation += 1

    # --------------------------------------------------------------- queries
    def agent(self, name: str) -> AgentDef:
        try:
            return self._snap.agents[name]
        except KeyError:
            raise KeyError(f"unknown agent '{name}' (known: {sorted(self._snap.agents)})") from None

    def select_tools(self, agent: AgentManifest) -> list[ToolDef]:
        """Tools available to `agent`: allow-globs minus deny-globs, hidden excluded.

        Deterministic order (builtins sorted, then dynamic sorted) keeps the tool block
        byte-stable for prompt caching (spec §9.2).
        """
        sel = agent.tools
        builtins = sorted(self._snap.tools.values(), key=lambda t: t.name)
        dynamic = sorted(
            (t for n, t in self._dynamic.items() if n not in self._snap.tools),
            key=lambda t: t.name,
        )
        out: list[ToolDef] = []
        for tool in [*builtins, *dynamic]:
            if tool.manifest.exposure == "hidden":
                continue
            if not any(fnmatch.fnmatchcase(tool.name, g) for g in sel.allow):
                continue
            if any(fnmatch.fnmatchcase(tool.name, g) for g in sel.deny):
                continue
            out.append(tool)
        return out

    def select_skills(self, agent: AgentManifest) -> list[SkillDef]:
        return [
            s
            for n, s in sorted(self._snap.skills.items())
            if any(fnmatch.fnmatchcase(n, g) for g in agent.skills)
        ]

    def describe(self) -> dict[str, list[dict[str, Any]]]:
        """Plain listing for CLI/API."""

        def row(kind: str, name: str, source: str, path: Path | None, desc: str) -> dict[str, Any]:
            return {
                "kind": kind,
                "name": name,
                "source": source,
                "path": str(path) if path else None,
                "description": desc,
            }

        out: dict[str, list[dict[str, Any]]] = {}
        out["tools"] = [
            row("tool", t.name, t.source, t.path, t.manifest.description)
            for t in sorted(self.tools.values(), key=lambda t: t.name)
        ]
        out["agents"] = [
            row("agent", a.name, a.source, a.path, a.manifest.description) for a in self.agents.values()
        ]
        out["skills"] = [
            row("skill", s.name, s.source, s.path, s.manifest.description) for s in self.skills.values()
        ]
        out["workflows"] = [
            row("workflow", n, w.source, w.path, w.manifest.description) for n, w in self.workflows.items()
        ]
        out["mcp"] = [
            row("mcp", n, m.source, m.path, m.manifest.transport) for n, m in self._snap.mcp.items()
        ]
        out["prompts"] = [
            row("prompt", n, p.source, p.path, f"order {p.section.order}")
            for n, p in self._snap.prompts.items()
        ]
        out["permissions"] = [
            row("permissions", p.path.stem, p.source, p.path, f"{len(p.manifest.rules)} rules")
            for p in self._snap.permission_layers
        ]
        return out
