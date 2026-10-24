"""Split `---`-delimited YAML frontmatter from a markdown body."""

from __future__ import annotations

from typing import Any

import yaml


class FrontmatterError(ValueError):
    def __init__(self, message: str, line: int | None = None) -> None:
        super().__init__(message)
        self.line = line


def split(text: str) -> tuple[dict[str, Any], str, int]:
    """Return `(data, body, body_start_line)`. Raises `FrontmatterError`."""
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise FrontmatterError("missing '---' frontmatter block at line 1", 1)
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            raw = "".join(lines[1:idx])
            body = "".join(lines[idx + 1 :])
            return _load(raw, offset=1), body.lstrip("\n"), idx + 2
    raise FrontmatterError("unterminated frontmatter (no closing '---')", 1)


def load_yaml(text: str) -> dict[str, Any]:
    return _load(text, offset=0)


def _load(raw: str, offset: int) -> dict[str, Any]:
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = (mark.line + 1 + offset) if mark is not None else None
        raise FrontmatterError(f"invalid YAML: {exc}", line) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise FrontmatterError("top level must be a mapping", 1 + offset)
    return data


def locate_key(text: str, key: str) -> int | None:
    """Best-effort 1-based line of `key:` in a YAML/frontmatter text."""
    for no, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip(" -")
        if stripped.startswith(f"{key}:"):
            return no
    return None
