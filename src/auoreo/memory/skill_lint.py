"""Skills are procedural memory: the validator shared by the `skill_manage` tool and
`auoreo skill lint` (spec §2.11 "Skill-writer / curator", §11.11).

Rules: `name` is kebab-case and matches its folder, `description` is 20–500 chars (it is
the only text the model sees before loading the skill, so it must say *when* to use it),
the body has at least 50 chars, and nothing looks like a secret.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from auoreo.core.redaction import PREFIXES
from auoreo.harness import frontmatter

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DESC_MIN, DESC_MAX, BODY_MIN, NAME_MAX = 20, 500, 50, 64
_PREFIX_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(p) for p in PREFIXES) + r")[A-Za-z0-9_\-]{8,}"
)


def looks_secret(text: str) -> bool:
    return bool(_PREFIX_RE.search(text)) or "PRIVATE KEY" in text


def lint(name: str, description: str, body: str, folder: str | None = None) -> list[str]:
    """Problems with a skill definition; empty when it is valid."""
    errors: list[str] = []
    if not NAME_RE.fullmatch(name) or len(name) > NAME_MAX:
        errors.append(f"name '{name}' must be kebab-case (a-z, 0-9, single hyphens), ≤ {NAME_MAX} chars")
    if folder is not None and folder != name:
        errors.append(f"name '{name}' must match its folder '{folder}'")
    desc = " ".join(description.split())
    if not DESC_MIN <= len(desc) <= DESC_MAX:
        errors.append(
            f"description is {len(desc)} chars; it must be {DESC_MIN}–{DESC_MAX} "
            "and say when to use the skill"
        )
    if len(body.strip()) < BODY_MIN:
        errors.append(f"body is {len(body.strip())} chars; it must be at least {BODY_MIN}")
    for label, text in (("name", name), ("description", description), ("body", body)):
        if looks_secret(text):
            errors.append(f"{label} looks like it contains a secret")
    return errors


def lint_file(path: Path) -> list[str]:
    """Lint `skills/<name>/SKILL.md` on disk."""
    try:
        data, body, _ = frontmatter.split(path.read_text(encoding="utf-8"))
    except (OSError, frontmatter.FrontmatterError) as exc:
        return [f"unreadable: {exc}"]
    return lint(str(data.get("name", "")), str(data.get("description", "")), body, path.parent.name)


def render(name: str, description: str, body: str) -> str:
    head = yaml.safe_dump(
        {"name": name, "description": " ".join(description.split())},
        sort_keys=False,
        allow_unicode=True,
        width=10_000,
    )
    return f"---\n{head}---\n{body.strip()}\n"
