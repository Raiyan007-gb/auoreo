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
