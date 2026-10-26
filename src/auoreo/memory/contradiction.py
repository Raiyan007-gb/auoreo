"""Contradiction flagging heuristic (spec §24.3, P1 minimal).

A new fact *may contradict* an existing active fact when both share at least three
distinctive tokens and they differ in polarity (`not`, `never`, `no longer`, `instead`,
`n't` in exactly one of them) or carry different integers. Flags are advisory: they are
emitted as `memory.contradiction` and written to the ledger; nothing is overwritten.
"""

from __future__ import annotations

import re

from auoreo.memory.store import STOPWORDS

MIN_SHARED = 3
_NEGATION = re.compile(r"\b(?:not|never|no longer|instead)\b|n't\b", re.I)
_INT = re.compile(r"(?<![\w.])\d+(?![\w.]*\d)")
_WORD = re.compile(r"[^\W\d_][\w-]*", re.U)
_IGNORED = STOPWORDS | {"not", "never", "no", "longer", "instead", "now", "use", "uses", "used"}


def tokens(text: str) -> set[str]:
    """Distinctive tokens: lowercase words ≥ 3 chars, no stopwords, no polarity words."""
    return {t for t in _WORD.findall(text.lower()) if len(t) >= 3 and t not in _IGNORED}


def top_terms(text: str, n: int = 8) -> list[str]:
    """The longest distinctive tokens (a cheap stand-in for IDF) for the FTS probe."""
    return sorted(tokens(text), key=lambda t: (-len(t), t))[:n]


def _negated(text: str) -> bool:
    return bool(_NEGATION.search(text))


def _ints(text: str) -> set[str]:
    return {m.lstrip("0") or "0" for m in _INT.findall(text)}


def contradicts(new: str, old: str) -> bool:
    if len(tokens(new) & tokens(old)) < MIN_SHARED:
        return False
    if _negated(new) != _negated(old):
        return True
    a, b = _ints(new), _ints(old)
    return bool(a and b and a != b)


# --- I4 (spec §11.6 / §24.3): entity overlap ≥ 0.3 + score ≥ 0.3 over the top-500 candidates
def features(text: str) -> set[str]:
    """Entities (memory.entities) ∪ distinctive tokens: the overlap vocabulary."""
    from auoreo.memory.entities import extract_entities

    return set(extract_entities(text, limit=32)) | tokens(text)


def entity_overlap(new: str, old: str) -> float:
    a, b = features(new), features(old)
    return len(a & b) / len(a | b) if a and b else 0.0


def score(new: str, old: str) -> float:
    """1.0 polarity flip, 0.6 different numbers, else 0 (only meaningful with overlap)."""
    if _negated(new) != _negated(old):
        return 1.0
    a, b = _ints(new), _ints(old)
    return 0.6 if a and b and a != b else 0.0


def is_contradiction(new: str, old: str, min_overlap: float = 0.3, min_score: float = 0.3) -> bool:
    return entity_overlap(new, old) >= min_overlap and score(new, old) >= min_score
