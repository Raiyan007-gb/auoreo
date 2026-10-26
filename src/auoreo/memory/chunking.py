"""Recursive token-bounded chunking: paragraph → sentence → word (spec §11.4)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

Counter = Callable[[str], int]
_SPLITTERS: tuple[str, ...] = (r"\n\s*\n", r"(?<=[.!?])\s+", r"\s+")


@dataclass(frozen=True)
class Chunk:
    text: str
    start_line: int
    end_line: int
    tokens: int
    span_start: int = -1
    """Character offset of the chunk's own text in the source document (−1 if unknown)."""
    span_end: int = -1


def _split(text: str, max_tokens: int, count: Counter, level: int = 0) -> list[str]:
    if count(text) <= max_tokens or level >= len(_SPLITTERS):
        return [text]
    parts = [p for p in re.split(_SPLITTERS[level], text) if p.strip()]
    out: list[str] = []
    buf = ""
    for part in parts:
        candidate = f"{buf}\n\n{part}" if buf and level == 0 else (f"{buf} {part}" if buf else part)
        if count(candidate) <= max_tokens:
            buf = candidate
            continue
        if buf:
            out.append(buf)
        if count(part) > max_tokens:
            out.extend(_split(part, max_tokens, count, level + 1))
            buf = ""
        else:
            buf = part
    if buf:
        out.append(buf)
    return out


def chunk_text(text: str, max_tokens: int, overlap: int, count: Counter) -> list[Chunk]:
    """Split into chunks of ≤ max_tokens; each chunk after the first is prefixed with the
    last `overlap` tokens' worth of words from its predecessor."""
    pieces = _split(text.strip(), max_tokens, count)
    chunks: list[Chunk] = []
    cursor = 0
    prev_tail = ""
    for piece in pieces:
        pos = text.find(piece[:80], cursor)
        start = text.count("\n", 0, pos) + 1 if pos >= 0 else 1
        end = start + piece.count("\n")
        span = (-1, -1)
        if pos >= 0:
            tail_at = text.rfind(piece[-40:], pos, pos + len(piece) + 200)
            span = (pos, tail_at + len(piece[-40:]) if tail_at >= 0 else pos + len(piece))
            cursor = pos + len(piece)
        body = f"[continued] {prev_tail} {piece}".strip() if prev_tail else piece
        chunks.append(Chunk(body, start, end, count(body), *span))
        words = piece.split()
        tail: list[str] = []
        while words and count(" ".join(tail)) < overlap:
            tail.insert(0, words.pop())
        prev_tail = " ".join(tail) if overlap > 0 else ""
    return chunks


# --- I4 (spec §11.4): docs tier — heading-aware, ~900 tokens, 15% overlap, code kept whole
_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+\S")


@dataclass(frozen=True)
class _Unit:
    text: str
    start: int  # char offsets in the source
    end: int
    code: bool = False
    heading: str | None = None


def _units(text: str) -> list[_Unit]:
    """Headings, fenced code blocks (whole) and paragraphs, with their char offsets."""
    out: list[_Unit] = []
    lines = text.splitlines(keepends=True)
    pos, i = 0, 0
    buf_start, buf = -1, ""

    def flush() -> None:
        nonlocal buf, buf_start
        if buf.strip():
            out.append(_Unit(buf.strip("\n"), buf_start, buf_start + len(buf.rstrip("\n"))))
        buf, buf_start = "", -1

    while i < len(lines):
        line = lines[i]
        if _FENCE.match(line):
            flush()
            start, block = pos, line
            fence = _FENCE.match(line).group(1)  # type: ignore[union-attr]
            pos += len(line)
            i += 1
            while i < len(lines):
                block += lines[i]
                pos += len(lines[i])
                i += 1
                if lines[i - 1].strip().startswith(fence):
                    break
            out.append(_Unit(block.rstrip("\n"), start, start + len(block.rstrip("\n")), code=True))
            continue
        if _HEADING.match(line):
            flush()
            out.append(_Unit(line.strip(), pos, pos + len(line.rstrip("\n")), heading=line.strip()))
        elif not line.strip():
            flush()
        else:
            if buf_start < 0:
                buf_start = pos
            buf += line
        pos += len(line)
        i += 1
    flush()
    return out


def chunk_docs(text: str, max_tokens: int, overlap_ratio: float, count: Counter) -> list[Chunk]:
    """Docs-tier chunks: split at headings, never inside a fenced code block (an oversized
    block becomes its own chunk), paragraphs over the cap split recursively; each chunk
    after the first carries the heading it belongs to and ~`overlap_ratio` of the previous
    chunk's trailing prose as `[continued]` context."""
    units: list[_Unit] = []
    for u in _units(text):
        if u.code or u.heading or count(u.text) <= max_tokens:
            units.append(u)
            continue
        cursor = u.start
        for piece in _split(u.text, max_tokens, count):
            at = text.find(piece[:40], cursor)
            at = at if at >= 0 else cursor
            units.append(_Unit(piece, at, at + len(piece)))
            cursor = at + len(piece)
    groups: list[tuple[list[_Unit], str | None]] = []
    current: list[_Unit] = []
    heading: str | None = None
    group_heading: str | None = None
    for u in units:
        candidate = "\n\n".join(x.text for x in [*current, u])
        if current and (u.heading is not None or count(candidate) > max_tokens):
            groups.append((current, group_heading))
            current = []
        if u.heading is not None:
            heading = u.heading
        if not current:
            group_heading = None if u.heading is not None else heading
        current.append(u)
    if current:
        groups.append((current, group_heading))
    overlap = int(max_tokens * overlap_ratio)
    chunks: list[Chunk] = []
    prev_tail = ""
    for members, head in groups:
        body = "\n\n".join(m.text for m in members)
        prefix = "\n".join(p for p in (head, f"[continued] {prev_tail}" if prev_tail else "") if p)
        full = f"{prefix}\n\n{body}" if prefix else body
        start, end = members[0].start, members[-1].end
        chunks.append(
            Chunk(full, text.count("\n", 0, start) + 1, text.count("\n", 0, end) + 1, count(full), start, end)
        )
        prose = [m for m in members if not m.code and m.heading is None]
        words = prose[-1].text.split() if prose and overlap > 0 else []
        tail: list[str] = []
        while words and count(" ".join(tail)) < overlap:
            tail.insert(0, words.pop())
        prev_tail = " ".join(tail)
    return chunks


def looks_like_docs(text: str, source: str = "") -> bool:
    """Markdown-ish documents (headings or code fences) use the docs tier under `tier=auto`."""
    if source.lower().endswith((".md", ".markdown", ".mdx", ".rst")):
        return True
    return any(_HEADING.match(ln) or _FENCE.match(ln) for ln in text.splitlines()[:400])
