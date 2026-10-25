"""Turn definitions into a runnable `AgentSpec` and run workflows.

Nothing here knows about specific agents or tools — it only interprets manifests.
"""

from __future__ import annotations

import contextlib
import platform
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auoreo.core.permissions import visible_tools
from auoreo.harness.loader import VAR_RE, AgentDef, FamilyDef, SkillDef, ToolDef
from auoreo.harness.manifests import Rule, Variant
from auoreo.harness.registry import Harness


class TemplateError(ValueError):
    pass


def render(template: str, values: dict[str, Any]) -> str:
    """Strict render: every `{{var}}` (dotted paths allowed) must resolve."""

    def sub(m: re.Match[str]) -> str:
        node: Any = values
        for part in m.group(1).split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                raise TemplateError(f"undefined template variable '{{{{{m.group(1)}}}}}'")
        return str(node)

    return VAR_RE.sub(sub, template)


@dataclass(frozen=True)
class AgentSpec:
    """Frozen per-turn view of an agent: what the runner needs, nothing more."""

    agent: AgentDef
    model: str
    tools: tuple[ToolDef, ...]
    deferred: tuple[ToolDef, ...]
    skills: tuple[SkillDef, ...]
    system_prompt: str  # tier: stable — byte-stable for the session
    context_template: str  # tier: context — rendered each turn
    rules: tuple[Rule, ...]
    variant: Variant | None
    max_steps: int
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.agent.name


def _tools_list(tools: list[ToolDef]) -> str:
    return "\n".join(f"- `{t.name}` — {t.manifest.description.strip()}" for t in tools) or "(none)"


def _skills_index(skills: list[SkillDef]) -> str:
    if not skills:
        return "(no skills installed)"
    return "\n".join(f"- `{s.name}` — {s.manifest.description.strip()}" for s in skills)


def pick_family(families: dict[str, FamilyDef], model: str) -> FamilyDef | None:
    fallback = None
    for fam in sorted(families.values(), key=lambda f: f.family.name):
        if not fam.family.match:
            fallback = fallback or fam
            continue
        if any(re.search(rx, model) for rx in fam.family.match):
            return fam
    return fallback


def pick_variant(harness: Harness, model: str) -> Variant | None:
    vf = harness.snapshot.variants
    if vf is None:
        return None
    from auoreo.core.settings import get_settings
    from auoreo.gateway.pipeline import match_variant
    from auoreo.gateway.resolve import primary_deployment

    with contextlib.suppress(Exception):  # a group/alias is shaped by its primary deployment
        model = primary_deployment(get_settings(), model)
    return match_variant(list(vf.manifest.variants), model)


def build_spec(
    harness: Harness,
    agent_name: str,
    model: str,
    permission_mode: str,
) -> AgentSpec:
    agent = harness.agent(agent_name)
    rules: list[Rule] = []
    for layer in harness.snapshot.permission_layers:
        rules.extend(layer.manifest.rules)
    rules.extend(agent.manifest.permissions)
    # §19.1: a tool whose last matching rule is `deny "*"` is hidden from the model.
    selected = visible_tools(harness.select_tools(agent.manifest), rules)
    direct = [t for t in selected if t.manifest.exposure == "direct"]
    deferred = [t for t in selected if t.manifest.exposure == "deferred"]
    skills = harness.select_skills(agent.manifest)

    stable_values = {
        "agent_name": agent.name,
        "agent_description": agent.manifest.description,
        "agent_instructions": agent.body,
        "tools_list": _tools_list(direct),
        "skills_index": _skills_index(skills),
        "permission_mode": permission_mode,
        "model": model,
    }
    stable: list[str] = []
    context: list[str] = []
    sections = sorted(harness.snapshot.prompts.values(), key=lambda p: (p.section.order, p.section.name))
    for sec in sections:
        if sec.section.role != "section":  # compaction / guardian are aux-call prompts
            continue
        if sec.section.tier == "stable":
            stable.append(render(sec.template, stable_values))
        else:
            context.append(sec.template)
    fam = pick_family(harness.snapshot.families, model)
    if fam and fam.text:
        stable.append(fam.text)

    return AgentSpec(
        agent=agent,
        model=model,
        tools=tuple(direct),
        deferred=tuple(deferred),
        skills=tuple(skills),
        system_prompt="\n\n".join(s for s in stable if s.strip()),
        context_template="\n\n".join(context),
        rules=tuple(rules),
        variant=pick_variant(harness, model),
        max_steps=agent.manifest.max_steps,
    )


def render_context(
    spec: AgentSpec,
    cwd: Path,
    permission_mode: str,
    memory: str = "",
    locale: str | None = None,
    tone: str = "",
) -> str:
    """Tier-2 context block (sent as a user-role snapshot each turn).

    `locale` and `tone` (the per-turn style lines from `auoreo.voice.tone.tone_of`) live
    here, never in a stable-tier section, so changing them cannot break the session's
    byte-stable system prompt. Unset locale → `spec.extras["locale"]` → `Settings.locale`."""
    if not spec.context_template:
        return ""
    if locale is None:
        locale = spec.extras.get("locale")
    if locale is None:
        from auoreo.core.settings import get_settings

        locale = get_settings().locale
    return render(
        spec.context_template,
        {
            "cwd": str(cwd),
            "model": spec.model,
            "date": datetime.now(UTC).date().isoformat(),
            "os": platform.system(),
            "permission_mode": permission_mode,
            "memory": memory,
            "agent_name": spec.name,
            "locale": locale or "",
            "tone": tone,
        },
    ).strip()


def floors(harness: Harness) -> list[str]:
    out: list[str] = []
    for layer in harness.snapshot.permission_layers:
        out.extend(layer.manifest.floors)
    return out
