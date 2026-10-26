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
    BackendDef,
    CommandDef,
    FamilyDef,
    Loaded,
    LoadError,
    PromptDef,
    SkillDef,
    ToolDef,
)
from auoreo.harness.manifests import AgentManifest
from auoreo.harness.skill_sources import ExternalSkill, SkillCatalog
from auoreo.tools.availability import Availability

log = logging.getLogger(__name__)

# Kinds merged by name (first root wins). Permissions are layered, not merged.
NAMED_KINDS = (
    "tools",
    "agents",
    "skills",
    "workflows",
    "mcp",
    "prompts",
    "families",
    "commands",
    "backends",
    "hooks",
    "jobs",
    "webhooks",
    "plugins",
    "policies",
)


class ToolCollisionError(Exception):
    """`error_on_collision`: two definitions of one tool name (spec §9.3)."""


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
    commands: dict[str, CommandDef] = field(default_factory=dict)
    backends: dict[str, BackendDef] = field(default_factory=dict)  # keyed "kind/name"
    hooks: dict[str, Loaded] = field(default_factory=dict)
    jobs: dict[str, Loaded] = field(default_factory=dict)
    webhooks: dict[str, Loaded] = field(default_factory=dict)
    plugins: dict[str, Loaded] = field(default_factory=dict)
    policies: dict[str, Loaded] = field(default_factory=dict)
    permission_layers: list[Loaded] = field(default_factory=list)  # low → high precedence
    variants: Loaded | None = None
    errors: list[LoadError] = field(default_factory=list)
    shadowed: list[Shadowed] = field(default_factory=list)


def _load_snapshot(roots: list[Root], strict_overlays: bool, error_on_collision: bool = False) -> Snapshot:
    snap = Snapshot()
    fatal: list[LoadError] = []

    def record(err: LoadError, source: str, collision: bool = False) -> None:
        snap.errors.append(err)
        if source == "bundled" or strict_overlays or (collision and error_on_collision):
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
                        collision=True,
                    )
                    target[name] = item  # keep the lower-precedence original
                elif kind == "tools":
                    log.warning("tool '%s' (%s) overrides the %s one", name, winner.source, cand.root.source)
                continue
            target[name] = item

    complete = sorted((p for p in snap.prompts.values() if p.section.complete), key=lambda p: p.section.name)
    for extra in complete[1:]:  # spec §12: exactly one `complete: true` section may exist
        msg = f"only one prompt section may set 'complete: true' (already: '{complete[0].section.name}')"
        record(LoadError(extra.path, msg), extra.source)

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

    def __init__(
        self, roots: list[Root], strict_overlays: bool = False, error_on_collision: bool = False
    ) -> None:
        self.roots = roots
        self.strict_overlays = strict_overlays
        self.error_on_collision = error_on_collision
        self._lock = threading.RLock()
        self._snap = _load_snapshot(roots, strict_overlays, error_on_collision)
        self._dynamic: dict[str, ToolDef] = {}  # e.g. MCP tools, keyed by name
        self._dynamic_errors: list[LoadError] = []
        self._ext_skills = SkillCatalog()  # plugin + MCP skills (spec §15 7-source catalog)
        self.availability = Availability()
        """`is_enabled` / `env_gate` / `check_fn` verdicts (services wires the secret store)."""
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
        """Folder skills only (they have a `SKILL.md` path); see `all_skills`."""
        return self._snap.skills

    @property
    def all_skills(self) -> dict[str, SkillDef | ExternalSkill]:
        """Folder skills merged with plugin/MCP skills (folder roots win a name clash)."""
        with self._lock:
            return self._ext_skills.merged(dict(self._snap.skills))[0]

    @property
    def skill_collisions(self) -> list[dict[str, str]]:
        with self._lock:
            return self._ext_skills.merged(dict(self._snap.skills))[1]

    @property
    def workflows(self) -> dict[str, Loaded]:
        return self._snap.workflows

    @property
    def commands(self) -> dict[str, CommandDef]:
        return self._snap.commands

    @property
    def hooks(self) -> dict[str, Loaded]:
        return self._snap.hooks

    @property
    def jobs(self) -> dict[str, Loaded]:
        return self._snap.jobs

    @property
    def webhooks(self) -> dict[str, Loaded]:
        return self._snap.webhooks

    @property
    def plugins(self) -> dict[str, Loaded]:
        return self._snap.plugins

    @property
    def policies(self) -> dict[str, Loaded]:
        return self._snap.policies

    @property
    def errors(self) -> list[LoadError]:
        return [*self._snap.errors, *self._dynamic_errors]

    # -------------------------------------------------------------- mutation
    def reload(self) -> dict[str, Any]:
        """Re-discover everything. A failed reload keeps the previous snapshot."""
        new = _load_snapshot(self.roots, self.strict_overlays, self.error_on_collision)
        self.availability.invalidate()
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
        """Builtins win on a name clash; with `error_on_collision` a clash raises
        `ToolCollisionError` (and is listed in `errors`) instead of a warning."""
        with self._lock:
            prev = self._snap.tools.get(tool.name) or self._dynamic.get(tool.name)
            if prev is not None and prev.source != tool.source:
                msg = f"{tool.source} tool '{tool.name}' collides with the {prev.source} one"
                if self.error_on_collision:
                    self._dynamic_errors.append(LoadError(Path(tool.source), msg))
                    raise ToolCollisionError(msg)
                log.warning("%s; %s", msg, "builtin wins" if tool.name in self._snap.tools else "replaced")
            self._dynamic[tool.name] = tool
            self.generation += 1

    def register_skills(self, source: str, skills: list[ExternalSkill]) -> list[dict[str, str]]:
        """Replace every skill of `source` (`plugin:<name>` | `mcp:<server>`); returns the
        name collisions this source loses (still reachable as `<source>:<name>`)."""
        with self._lock:
            self._ext_skills.set(source, skills)
            self.generation += 1
            lost = [c for c in self._ext_skills.merged(dict(self._snap.skills))[1] if c["loser"] == source]
        for c in lost:
            log.warning("skill '%s' from %s is shadowed by %s", c["name"], source, c["winner"])
        return lost

    def unregister_skills(self, source: str) -> None:
        with self._lock:
            if self._ext_skills.drop(source):
                self.generation += 1

    def resolve_skill(self, name: str) -> SkillDef | ExternalSkill | None:
        """Bare name (merged catalog) or qualified `<source>:<name>` (also shadowed ones)."""
        with self._lock:
            merged = self._ext_skills.merged(dict(self._snap.skills))[0]
            if name in merged:
                found: SkillDef | ExternalSkill = merged[name]
                return found
            hit = self._ext_skills.get(name)
            if hit is not None:
                return hit
            src, _, bare = name.rpartition(":")
            folder = self._snap.skills.get(bare)
            return folder if folder is not None and folder.source == src else None

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
            if tool.manifest.exposure == "hidden" or self.availability.reason(tool) is not None:
                continue
            if not any(fnmatch.fnmatchcase(tool.name, g) for g in sel.allow):
                continue
            if any(fnmatch.fnmatchcase(tool.name, g) for g in sel.deny):
                continue
            out.append(tool)
        return out

    def select_skills(self, agent: AgentManifest) -> list[SkillDef | ExternalSkill]:
        return [
            s
            for n, s in sorted(self.all_skills.items())
            if any(fnmatch.fnmatchcase(n, g) for g in agent.skills)
        ]

    # -------------------------------------------------------------- backends
    def backends_for(self, kind: str) -> dict[str, BackendDef]:
        return {d.manifest.name: d for k, d in self._snap.backends.items() if d.manifest.kind == kind}

    def select_backend(self, kind: str, chosen: str | None) -> BackendDef | None:
        """settings.backends[kind] → manifest `default: true` → the only candidate → None."""
        cands = self.backends_for(kind)
        if chosen is not None:
            if chosen not in cands:
                raise KeyError(f"backend '{kind}/{chosen}' not found (known: {sorted(cands)})")
            return cands[chosen]
        defaults = [d for d in cands.values() if d.manifest.default]
        if len(defaults) > 1:
            names = sorted(d.manifest.name for d in defaults)
            raise KeyError(f"several '{kind}' backends claim default: {names}")
        if defaults:
            return defaults[0]
        return next(iter(cands.values())) if len(cands) == 1 else None

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

        def tool_row(t: ToolDef) -> dict[str, Any]:
            why = self.availability.reason(t)
            extra = {"available": why is None, "unavailable_reason": why, "emoji": t.manifest.emoji}
            return {**row("tool", t.name, t.source, t.path, t.manifest.description), **extra}

        out: dict[str, list[dict[str, Any]]] = {}
        out["tools"] = [tool_row(t) for t in sorted(self.tools.values(), key=lambda t: t.name)]
        out["agents"] = [
            row("agent", a.name, a.source, a.path, a.manifest.description) for a in self.agents.values()
        ]
        out["skills"] = [
            row("skill", s.name, s.source, s.path, s.manifest.description) for s in self.all_skills.values()
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
        out["commands"] = [
            row("command", n, c.source, c.path, c.manifest.description)
            for n, c in sorted(self._snap.commands.items())
        ]
        out["backends"] = [
            row(
                "backend",
                n,
                b.source,
                b.path,
                f"{b.manifest.description}" + (" (default)" if b.manifest.default else ""),
            )
            for n, b in sorted(self._snap.backends.items())
        ]
        for kind in ("hooks", "jobs", "webhooks", "plugins", "policies"):
            out[kind] = [
                row(
                    {"policies": "policy"}.get(kind, kind[:-1]),
                    n,
                    d.source,
                    d.path,
                    f"{d.manifest.description}" + ("" if d.manifest.enabled else " (disabled)"),
                )
                for n, d in sorted(getattr(self._snap, kind).items())
            ]
        out["permissions"] = [
            row("permissions", p.path.stem, p.source, p.path, f"{len(p.manifest.rules)} rules")
            for p in self._snap.permission_layers
        ]
        return out
