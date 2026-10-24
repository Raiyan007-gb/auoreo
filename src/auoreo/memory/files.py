"""Memory markdown files with hard caps (spec §11.2). Truncation names the cap that fired."""

from __future__ import annotations

import shutil
from pathlib import Path

from auoreo.core.paths import bundled_root

FILES = ("MEMORY.md", "USER.md", "PROJECT.md", "PREFS.md")
INDEX_MAX_LINES = 200
INDEX_MAX_BYTES = 25_000


def memory_dir(home: Path) -> Path:
    d = home / "memory"
    if not d.is_dir():
        d.mkdir(parents=True, exist_ok=True)
        src = bundled_root() / "memory"
        for name in FILES:
            if (src / name).is_file() and not (d / name).exists():
                shutil.copyfile(src / name, d / name)
    return d


def truncate(
    text: str,
    *,
    max_chars: int | None = None,
    max_lines: int | None = None,
    max_bytes: int | None = None,
    label: str,
) -> str:
    """Apply caps, cut at the last newline, and append a warning naming the cap."""
    reasons: list[str] = []
    if max_lines is not None and text.count("\n") + 1 > max_lines:
        text = "\n".join(text.splitlines()[:max_lines])
        reasons.append(f"{max_lines} lines")
    if max_bytes is not None and len(text.encode()) > max_bytes:
        text = text.encode()[:max_bytes].decode(errors="ignore")
        text = text[: text.rfind("\n")] if "\n" in text else text
        reasons.append(f"{max_bytes} bytes")
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars]
        text = text[: text.rfind("\n")] if "\n" in text else text
        reasons.append(f"{max_chars} chars")
    if reasons:
        text += f"\n> WARNING: {label} exceeds its cap ({', '.join(reasons)}). Only part loaded."
    return text


def read_capped(home: Path, name: str, max_chars: int) -> str:
    path = memory_dir(home) / name
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    if name == "MEMORY.md":
        text = truncate(text, max_lines=INDEX_MAX_LINES, max_bytes=INDEX_MAX_BYTES, label=name)
    return truncate(text, max_chars=max_chars, label=name)
