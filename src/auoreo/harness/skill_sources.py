"""Skills from non-folder sources (spec §2.11/§15): plugins and MCP servers.

Folder skills (runtime → project → bundled → extra roots) are merged by the loader; this
module adds the dynamic sources on top. Precedence is by source rank (lower wins) — every
folder root beats a plugin, a plugin beats an MCP server — and within one rank the first
registration wins. A losing skill stays reachable under its qualified name
`<source>:<name>` (e.g. `plugin:demo:review`, `mcp:docs:style`) and is listed in
`Harness.skill_collisions`.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auoreo.harness.manifests import SLUG_RE, SkillManifest

SOURCE_RANK: dict[str, int] = {"runtime": 250, "project": 300, "bundled": 600, "plugin": 700, "mcp": 800}
"""Spec §15 ranks (lower = higher precedence); `extra:<n>` roots rank with bundled + n."""

MENTION_RE = re.compile(r"(?<![\w@./-])@([a-z0-9][a-z0-9_-]*(?:\.[a-z0-9_-]+)*(?::[A-Za-z0-9_.:-]+)?)")
"""`@skill-name` (or a qualified `@plugin:demo:review`) at a word boundary; emails are skipped."""


def rank(source: str) -> int:
    kind, _, rest = source.partition(":")
    if kind == "extra":
        return SOURCE_RANK["bundled"] + 1 + (int(rest) if rest.isdigit() else 0)
    return SOURCE_RANK.get(kind, 900)


@dataclass(frozen=True)
class ExternalSkill:
    """A skill offered by a plugin (`source="plugin:<name>"`) or an MCP server
    (`source="mcp:<server>"`). The body is inline (`text`) or fetched on load (`fetch`)."""

    manifest: SkillManifest
    source: str
    text: str | None = None
    fetch: Callable[[], Awaitable[str]] | None = field(default=None, compare=False, repr=False)
    path: Path | None = None

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def qualified(self) -> str:
        return f"{self.source}:{self.name}"

    def body(self) -> str:
        return self.text or ""

    async def load(self) -> str:
        if self.fetch is not None:
            return await self.fetch()
        return self.body()


def make_skill(
    source: str, name: str, description: str, *, text: str | None = None, fetch: Any = None
) -> ExternalSkill:
    """Validate a plugin/MCP skill (kebab-case name; an empty description falls back to the name)."""
    if not re.fullmatch(SLUG_RE, name):
        raise ValueError(f"{source}: skill name '{name}' is not kebab-case")
    manifest = SkillManifest(name=name, description=(description or name).strip()[:1024] or name)
    return ExternalSkill(manifest, source, text=text, fetch=fetch)


async def skill_text(skill: Any) -> str:
    """Full body of a folder skill (`SkillDef`) or an external one."""
    load = getattr(skill, "load", None)
    if load is not None:
        return str(await load())
    return str(skill.body())


def supporting_files(skill: Any, limit: int = 50) -> list[str]:
    path = getattr(skill, "path", None)
    if path is None:
        return []
    return sorted(
        str(p.relative_to(path.parent))
        for p in path.parent.rglob("*")
        if p.is_file() and p.name != "SKILL.md"
    )[:limit]


async def skill_message(skill: Any) -> str:
    """The user message a loaded skill is injected as (never a system edit: cache-stable)."""
    files = supporting_files(skill)
    folder = getattr(skill, "path", None)
    extra = f"\nSupporting files in {folder.parent}: {', '.join(files)}" if files and folder else ""
    return f'<skill name="{skill.name}">\n{(await skill_text(skill)).strip()}{extra}\n</skill>'


def find_skill(spec: Any, harness: Any, name: str) -> Any | None:
    """Resolve `name` for one turn: the agent's selected skills by bare name, then a
    qualified `<source>:<name>` that the agent's skill globs allow and the session has
    not disabled."""
    if spec is not None:
        allowed = {s.name: s for s in spec.skills}
        if name in allowed:
            return allowed[name]
    elif harness is not None and name in harness.all_skills:
        return harness.all_skills[name]
    if harness is None or ":" not in name:
        return None
    skill = harness.resolve_skill(name)
    if skill is None:
        return None
    if spec is not None:
        globs = spec.agent.manifest.skills
        disabled = set(spec.extras.get("disabled_sources", ()))
        if not any(fnmatch.fnmatchcase(skill.name, g) for g in globs) or skill.source in disabled:
            return None
    return skill


def mentions(text: str) -> list[str]:
    """Distinct `@name` mentions in order of appearance."""
    seen: list[str] = []
    for m in MENTION_RE.finditer(text):
        name = m.group(1).rstrip(".:-")
        if name not in seen:
            seen.append(name)
    return seen


async def mention_messages(spec: Any, harness: Any, text: str) -> list[tuple[str, str]]:
    """`(skill name, injected message)` for every `@skill` the user mentioned this turn."""
    out: list[tuple[str, str]] = []
    for name in mentions(text):
        skill = find_skill(spec, harness, name)
        if skill is None or any(n == skill.name for n, _ in out):
            continue
        try:
            out.append((skill.name, await skill_message(skill)))
        except Exception:  # an unreachable MCP server must not fail the turn
            continue
    return out


class SkillCatalog:
    """External skills keyed by qualified name, merged under the folder skills."""

    def __init__(self) -> None:
        self._by_source: dict[str, list[ExternalSkill]] = {}

    def set(self, source: str, skills: Iterable[ExternalSkill]) -> None:
        items = [s for s in skills if s.source == source]
        if items:
            self._by_source[source] = items
        else:
            self._by_source.pop(source, None)

    def drop(self, source: str) -> bool:
        return self._by_source.pop(source, None) is not None

    def all(self) -> list[ExternalSkill]:
        """Every external skill, highest precedence first (stable within a rank)."""
        order = sorted(self._by_source, key=rank)  # dict order = registration order
        return [s for src in order for s in self._by_source[src]]

    def merged(self, folder: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
        """`(name → winning skill, collisions)` with folder skills first."""
        out: dict[str, Any] = dict(folder)
        collisions: list[dict[str, str]] = []
        for skill in self.all():
            winner = out.get(skill.name)
            if winner is not None:
                collisions.append(
                    {
                        "name": skill.name,
                        "winner": winner.source,
                        "loser": skill.source,
                        "qualified": skill.qualified,
                    }
                )
                continue
            out[skill.name] = skill
        return out, collisions

    def get(self, qualified: str) -> ExternalSkill | None:
        return next((s for s in self.all() if s.qualified == qualified), None)

    def sources(self) -> list[str]:
        return sorted(self._by_source, key=rank)


async def mcp_skill_defs(bridge: Any, server: str) -> list[ExternalSkill]:
    """`skill://<name>` / `<scheme>://skills/<name>` resources of one MCP server as skills."""
    from auoreo.skillhub.hub import MCP_SKILL_URI

    try:
        resources = await bridge.list_resources(server)
    except Exception:  # server without resources support
        return []
    out: list[ExternalSkill] = []
    for r in resources:
        m = MCP_SKILL_URI.match(str(r.uri))
        if not m:
            continue
        name = m.group("name").lower()
        uri = str(r.uri)

        async def fetch(uri: str = uri) -> str:
            contents = await bridge.read_resource(server, uri)
            return "\n".join(str(getattr(c, "text", "")) for c in contents if getattr(c, "text", None))

        try:
            out.append(make_skill(f"mcp:{server}", name, getattr(r, "description", None) or "", fetch=fetch))
        except ValueError:
            continue
    return out
