"""Text shaping before synthesis: `prepare_spoken_text` (spec §25.3 — TTS input MUST be free
of markdown and emoji) and `chunk_text` (sentence-boundary chunks under a backend's cap)."""

from __future__ import annotations

import re

_FENCE = re.compile(r"```.*?(```|$)", re.S)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_HTML = re.compile(r"</?[a-zA-Z][^>]*>")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_QUOTE = re.compile(r"^\s{0,3}>\s?", re.M)
_BULLET = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+", re.M)
_RULE = re.compile(r"^\s*([-*_]\s*){3,}$", re.M)
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}.*$", re.M)
_EMPHASIS = re.compile(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1")
_EMOJI = re.compile(
    "["
    "\U0001f000-\U0001faff"  # pictographs, emoticons, transport, symbols & pictographs ext.
    "\U00002600-\U000027bf"  # misc symbols, dingbats
    "\U0001f1e6-\U0001f1ff"  # flags
    "‍️⃣"  # ZWJ, variation selector, keycap
    "]+"
)
ABBREVIATIONS: dict[str, str] = {
    "e.g.": "for example",
    "i.e.": "that is",
    "etc.": "et cetera",
    "vs.": "versus",
    "approx.": "approximately",
    "Dr.": "Doctor",
    "Mr.": "Mister",
    "Mrs.": "Missus",
    "Ms.": "Miz",
    "No.": "number",
    "&": "and",
}
_ABBR = re.compile(
    "|".join(rf"(?<!\w){re.escape(k)}" + (r"(?!\w)" if k[-1].isalnum() else "") for k in ABBREVIATIONS)
)
_SENTENCE = re.compile(r"(?<=[.!?…])\s+")
_SPACE_PUNCT = re.compile(r"\s+([.,!?;:])")


def prepare_spoken_text(text: str) -> str:
    """Strip markdown, code, links, HTML and emoji; expand simple abbreviations."""
    t = _FENCE.sub(" code block omitted. ", text)
    t = _IMAGE.sub(r"\1", t)
    t = _LINK.sub(r"\1", t)
    t = _URL.sub("link", t)
    t = _INLINE_CODE.sub(r"\1", t)
    t = _HTML.sub(" ", t)
    t = _TABLE_SEP.sub("", t)
    t = _RULE.sub("", t)
    t = _HEADING.sub("", t)
    t = _QUOTE.sub("", t)
    t = _BULLET.sub("", t)
    for _ in range(2):  # nested emphasis
        t = _EMPHASIS.sub(r"\2", t)
    t = _EMOJI.sub("", t)
    t = t.replace("|", ", ")
    t = _ABBR.sub(lambda m: ABBREVIATIONS[m.group(0)], t)
    lines = [" ".join(line.split()) for line in t.splitlines()]
    paragraphs: list[str] = []
    for line in lines:
        if not line:
            continue
        if paragraphs and not paragraphs[-1].endswith((".", "!", "?", ":", ";", ",", "…")):
            paragraphs[-1] += "."
        paragraphs.append(line)
    return _SPACE_PUNCT.sub(r"\1", " ".join(paragraphs)).strip()


def chunk_text(text: str, limit: int) -> list[str]:
    """Split at sentence boundaries into chunks ≤ `limit` chars; a single over-long sentence
    is split at the last space (or hard) below the limit."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    current = ""
    for sentence in _SENTENCE.split(text):
        for piece in _hard_split(sentence, limit):
            if not current:
                current = piece
            elif len(current) + 1 + len(piece) <= limit:
                current = f"{current} {piece}"
            else:
                chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    return chunks


def _hard_split(sentence: str, limit: int) -> list[str]:
    out: list[str] = []
    while len(sentence) > limit:
        cut = sentence.rfind(" ", 0, limit + 1)
        cut = cut if cut > 0 else limit
        out.append(sentence[:cut].strip())
        sentence = sentence[cut:].strip()
    if sentence:
        out.append(sentence)
    return out
