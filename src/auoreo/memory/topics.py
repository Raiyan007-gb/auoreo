"""Memory file contracts (spec §11.2, §11.3): topic files, `memory_read`, drift guard.

- `topics/<slug>.md` carry `name / description / type` frontmatter (`type` from the closed
  taxonomy `user | feedback | project | reference`); MEMORY.md stays an index of
  `- [Title](topics/x.md) — hook` lines (hook ≤ 150 chars).
- `read_file(home, name)` serves `memory_read`: the full file (≤ 64 KB), not the capped
  snapshot that rides in the prompt.
- Drift guard (`guarded_write`): before overwriting a memory file, an unparseable file
  (bad UTF-8, broken topic frontmatter) is snapshotted to `<file>.bak.<ts>` and the write is
  refused; a file edited outside the memory tool since our last write is snapshotted to
  `.bak.<ts>` and then overwritten (nothing is silently lost).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path

import yaml

from auoreo.memory.files import FILES, memory_dir, truncate

TYPES = ("user", "feedback", "project", "reference")
TOPIC_DIR = "topics"
STATE_FILE = ".write_state.json"
READ_MAX_BYTES = 65_536
HOOK_MAX = 150
_SLUG = re.compile(r"[^a-z0-9_-]+")


class DriftError(Exception):
    """The file on disk cannot be parsed; it was snapshotted and the write refused."""


def slug(name: str) -> str:
    s = _SLUG.sub("-", name.strip().lower()).strip("-")
    if not s:
        raise ValueError("topic name needs letters or digits")
    return s[:64]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _state(mdir: Path) -> dict[str, str]:
    try:
        data = json.loads((mdir / STATE_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def parse_topic(text: str) -> dict[str, str]:
    """Frontmatter of a topic file; raises ValueError when missing or malformed."""
    if not text.startswith("---\n") or "\n---" not in text[4:]:
        raise ValueError("topic file has no frontmatter")
    head = text[4 : text.index("\n---", 4)]
    data = yaml.safe_load(head) or {}
    if not isinstance(data, dict) or not data.get("name") or data.get("type") not in TYPES:
        raise ValueError("topic frontmatter needs name and type (user|feedback|project|reference)")
    return {k: str(v) for k, v in data.items()}


def snapshot(path: Path) -> Path:
    bak = path.with_name(f"{path.name}.bak.{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1000:03d}")
    shutil.copy2(path, bak)
    return bak


def guarded_write(path: Path, text: str) -> Path | None:
    """Write `text`, returning the `.bak.<ts>` snapshot taken (None when none was needed)."""
    mdir = path.parent if path.parent.name != TOPIC_DIR else path.parent.parent
    key = str(path.relative_to(mdir))
    state = _state(mdir)
    bak: Path | None = None
    if path.is_file():
        try:
            current = path.read_text(encoding="utf-8")
            if path.parent.name == TOPIC_DIR:
                parse_topic(current)
        except (UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
            bak = snapshot(path)
            raise DriftError(
                f"{key} is unparseable ({exc}); saved {bak.name} and refused to overwrite"
            ) from exc
        if key in state and state[key] != _sha(current):  # edited outside the memory tool
            bak = snapshot(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    state[key] = _sha(text)
    (mdir / STATE_FILE).write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    return bak


def topic_path(home: Path, name: str) -> Path:
    return memory_dir(home) / TOPIC_DIR / f"{slug(name)}.md"


def render_topic(name: str, description: str, type_: str, body: str) -> str:
    if type_ not in TYPES:
        raise ValueError(f"type must be one of {', '.join(TYPES)}")
    head = yaml.safe_dump(
        {"name": name, "description": description[:HOOK_MAX], "type": type_}, sort_keys=False
    )
    return f"---\n{head}---\n{body.strip()}\n"


def index_line(name: str, description: str) -> str:
    return f"- [{name}]({TOPIC_DIR}/{slug(name)}.md) — {description.strip()[:HOOK_MAX]}"


def upsert_index(index: str, name: str, description: str) -> str:
    """MEMORY.md with exactly one index line for the topic (replaced in place or appended)."""
    target = f"]({TOPIC_DIR}/{slug(name)}.md)"
    lines = index.rstrip("\n").splitlines()
    new = index_line(name, description)
    for i, line in enumerate(lines):
        if target in line:
            lines[i] = new
            return "\n".join(lines) + "\n"
    return "\n".join([*lines, new]) + "\n"


def drop_index(index: str, name: str) -> str:
    target = f"]({TOPIC_DIR}/{slug(name)}.md)"
    return "\n".join(ln for ln in index.splitlines() if target not in ln) + "\n"


def list_topics(home: Path) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for path in sorted((memory_dir(home) / TOPIC_DIR).glob("*.md")):
        try:
            meta = parse_topic(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError):
            meta = {"name": path.stem, "type": "?", "description": "(unparseable frontmatter)"}
        out.append({"file": f"{TOPIC_DIR}/{path.name}", **meta})
    return out


def read_file(home: Path, name: str) -> str:
    """Full text of a memory file or topic for `memory_read` (≤ 64 KB)."""
    mdir = memory_dir(home)
    if name in FILES:
        path = mdir / name
    else:
        stem = name.removeprefix(f"{TOPIC_DIR}/").removesuffix(".md")
        path = mdir / TOPIC_DIR / f"{slug(stem)}.md"
    if not path.is_file():
        raise FileNotFoundError(name)
    text = path.read_text(encoding="utf-8", errors="replace")
    return truncate(text, max_bytes=READ_MAX_BYTES, label=name)


__all__ = [
    "TYPES",
    "DriftError",
    "drop_index",
    "guarded_write",
    "list_topics",
    "parse_topic",
    "read_file",
    "render_topic",
    "slug",
    "topic_path",
    "upsert_index",
]
