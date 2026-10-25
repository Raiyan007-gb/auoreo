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
