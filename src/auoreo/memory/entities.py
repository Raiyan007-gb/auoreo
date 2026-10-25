"""Rule-based entity extraction for GraphRAG-lite (spec §25.8). Deterministic, no LLM.

Recognised, in priority order: URL hosts, `@handles`, `#tags`, file paths, quoted or
back-ticked terms, CamelCase and snake_case identifiers, capitalised multi-word spans and
capitalised single words that do not start a sentence. Every entity is canonicalised
(lower-case, edge punctuation and possessive `'s` stripped, whitespace collapsed), checked
against a stop-list and de-duplicated; at most `MAX_ENTITIES` are kept per text.
"""

from __future__ import annotations

import re

from auoreo.memory.store import STOPWORDS

MAX_ENTITIES = 12

STOP = STOPWORDS | frozenset(
    """
    also after before but can could each every here if into just more most no not now only
    other over same should so some such than then there these they those through under up
    very would yes user users today tomorrow yesterday monday tuesday wednesday thursday friday
    saturday sunday january february march april may june july august september october
    november december please thanks ok okay
    """.split()  # noqa: SIM905 — compact word list
)
SENTENCE_STOP = frozenset(
    """
    add all always any ask avoid both call calendar check code coding commit commits create
    deploy deploys docs don't dont every first he her his install keep last let logs make many
    maybe meetings never new next note notes old one ping prefer pull release remember run
    schedule see send set settings she start stop tests there their try two use usually write
    """.split()  # noqa: SIM905 — capitalised only because they open a sentence
)
"""A capitalised word that opens a sentence counts as an entity unless it is listed here."""

_URL = re.compile(r"https?://([^/\s:?#]+)[^\s]*", re.I)
_HANDLE = re.compile(r"(?<![\w@])@([A-Za-z0-9_][\w.-]*\w)")
_TAG = re.compile(r"(?<![\w#&])#([A-Za-z][\w-]*)")
_PATH = re.compile(
    r"(?<![\w/])(?:~?/)?(?:[\w.-]+/)+[\w.-]+"
    r"|\b[\w-]+\.(?:py|js|ts|tsx|jsx|md|yaml|yml|json|toml|txt|rs|go|sh|sql|html|css|ini|cfg|lock)\b"
)
_QUOTED = re.compile(r"\"([^\"\n]{2,40})\"|“([^”\n]{2,40})”|`([^`\n]{2,40})`")
_CAMEL = re.compile(r"\b[A-Za-z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b|\b[A-Z]{2,}[a-z][A-Za-z0-9]*\b")
_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_CAP_WORD = r"[A-Z][\w'’-]*"
_CAP_SPAN = re.compile(rf"\b{_CAP_WORD}(?:\s+(?:of|de|da|von|van|the)?\s*{_CAP_WORD})+")
_CAP_SINGLE = re.compile(rf"\b{_CAP_WORD}")
_SENTENCE_START = re.compile(r"(?:^|[.!?:;]\s+|\n\s*)$")


def canonical(raw: str) -> str:
    text = " ".join(raw.split()).strip(" \t.,;:!?()[]{}<>\"'`“”’")
    text = re.sub(r"['’]s$", "", text)
    return text.lower()


def _ok(entity: str) -> bool:
    if len(entity) < 2 or len(entity) > 80 or entity.isdigit():
        return False
    words = entity.split()
    return not all(w in STOP for w in words)


def _blank(text: str, span: tuple[int, int]) -> str:
    return text[: span[0]] + " " * (span[1] - span[0]) + text[span[1] :]


def extract_entities(text: str, limit: int = MAX_ENTITIES) -> list[str]:
    """Canonical entities of `text`, in first-seen priority order, ≤ `limit`."""
    found: list[str] = []

    def add(raw: str) -> None:
        ent = canonical(raw)
        if ent.startswith("www."):
            ent = ent[4:]
        if _ok(ent) and ent not in found:
            found.append(ent)

    work = text
    for m in list(_URL.finditer(work)):
        add(m.group(1))
        work = _blank(work, m.span())
    for rx in (_HANDLE, _TAG):
        for m in list(rx.finditer(work)):
            add(m.group(0))
            work = _blank(work, m.span())
    for m in list(_PATH.finditer(work)):
        add(m.group(0))
        work = _blank(work, m.span())
    for m in _QUOTED.finditer(work):
        add(next(g for g in m.groups() if g))
    for rx in (_CAMEL, _SNAKE):
        for m in rx.finditer(work):
            add(m.group(0))
    covered: list[tuple[int, int]] = []
    for m in _CAP_SPAN.finditer(work):
        words = m.group(0).split()
        if _SENTENCE_START.search(work[: m.start()]) and words[0].lower() in STOP:
            add(" ".join(words[1:]))  # "The Rust Book" at a sentence start → "rust book"
        else:
            add(m.group(0))
        covered.append(m.span())
    for m in _CAP_SINGLE.finditer(work):
        if any(a <= m.start() < b for a, b in covered):
            continue
        if _SENTENCE_START.search(work[: m.start()]) and canonical(m.group(0)) in SENTENCE_STOP:
            continue
        add(m.group(0))
    return found[:limit]


def query_entities(query: str) -> list[str]:
    """Candidate entities for a query: extracted ones plus 1–3-word n-grams of content words
    (queries are often lower-case); the caller resolves them against `fact_entities`."""
    out = extract_entities(query, limit=32)
    words = [canonical(w) for w in re.findall(r"[\w@#./'’-]+", query)]
    words = [w for w in words if w]
    for n in (3, 2, 1):
        for i in range(len(words) - n + 1):
            gram = words[i : i + n]
            if gram[0] in STOP or gram[-1] in STOP:
                continue
            cand = " ".join(gram)
            if _ok(cand) and cand not in out:
                out.append(cand)
    return out[:64]
