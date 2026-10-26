"""STT hallucination / echo filter (spec §13.3, §25.5).

Whisper-family models invent text on silence or noise ("Thank you for watching.") and loop
("the the the the"); without headphones the agent also hears its own TTS. `verdict()` checks a
transcript before it is injected anywhere (turn, steer, wake routing):

- **blocklist**: the whole utterance (lower-cased, punctuation stripped) is a known hallucination;
- **repeat**: one word or a 2–4 word phrase repeated `repeat_min` times in a row;
- **no_speech**: the backend flagged the result as non-speech (re-listen instead of injecting);
- **echo**: similarity ≥ `echo_similarity` (default 0.6) with something the agent said within
  `echo_window_s` (sliding window over its recent TTS texts) — dropped BEFORE steer/inject.

Pure functions plus `EchoMemory` (per session, monotonic timestamps); no audio libraries.
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

_PUNCT = re.compile(r"[^\w\s']+", re.UNICODE)
_WS = re.compile(r"\s+")


def norm(text: str) -> str:
    return _WS.sub(" ", _PUNCT.sub(" ", text.lower())).strip()


def repeat_loop(text: str, min_repeats: int = 4) -> bool:
    """True when a 1–4 word unit repeats `min_repeats` times back to back."""
    words = norm(text).split()
    for size in range(1, 5):
        need = size * min_repeats
        for start in range(0, max(0, len(words) - need + 1)):
            unit = words[start : start + size]
            if all(words[start + k * size : start + (k + 1) * size] == unit for k in range(min_repeats)):
                return True
    return False


def similarity(a: str, b: str) -> float:
    """Word-level similarity 0..1; also catches a transcript that is a slice of a longer reply."""
    wa, wb = norm(a).split(), norm(b).split()
    if not wa or not wb:
        return 0.0
    whole = SequenceMatcher(None, wa, wb, autojunk=False).ratio()
    short, long_ = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    if len(short) < 3:
        return whole
    best = 0.0
    for i in range(0, len(long_) - len(short) + 1):
        window = long_[i : i + len(short)]
        best = max(best, SequenceMatcher(None, short, window, autojunk=False).ratio())
    return max(whole, best)


class EchoMemory:
    """What the agent said recently, per session (`None` = any session)."""

    def __init__(self, window_s: float = 20.0, max_items: int = 32) -> None:
        self.window_s = window_s
        self._items: dict[str | None, deque[tuple[float, str]]] = {}
        self._max = max_items

    def add(self, session_id: str | None, text: str, now: float | None = None) -> None:
        if not text.strip():
            return
        q = self._items.setdefault(session_id, deque(maxlen=self._max))
        q.append((time.monotonic() if now is None else now, text))

    def recent(self, session_id: str | None, now: float | None = None) -> list[str]:
        now = time.monotonic() if now is None else now
        out: list[str] = []
        for key in {session_id, None}:
            out += [t for ts, t in self._items.get(key, ()) if now - ts <= self.window_s]
        return out

    def max_similarity(self, session_id: str | None, text: str, now: float | None = None) -> float:
        return max((similarity(text, said) for said in self.recent(session_id, now)), default=0.0)


@dataclass(frozen=True)
class Verdict:
    keep: bool
    reason: str = ""
    """`blocklist` | `repeat` | `no_speech` | `echo` | `empty` when dropped."""
    score: float = 0.0


def verdict(
    text: str,
    cfg: Any,
    *,
    no_speech: bool = False,
    echo: EchoMemory | None = None,
    session_id: str | None = None,
    now: float | None = None,
) -> Verdict:
    """Keep or drop one transcript; `cfg` is `settings.i8.stt`."""
    if not getattr(cfg, "filter_enabled", True):
        return Verdict(bool(text.strip()), "" if text.strip() else "empty")
    n = norm(text)
    if not n:
        return Verdict(False, "empty")
    if no_speech:
        return Verdict(False, "no_speech")
    if n in {norm(b) for b in getattr(cfg, "blocklist", [])}:
        return Verdict(False, "blocklist")
    if repeat_loop(text, int(getattr(cfg, "repeat_min", 4))):
        return Verdict(False, "repeat")
    if echo is not None:
        score = echo.max_similarity(session_id, text, now)
        if score >= float(getattr(cfg, "echo_similarity", 0.6)):
            return Verdict(False, "echo", round(score, 3))
    return Verdict(True)


def gate_segment(no_speech_prob: float | None, avg_logprob: float | None, cfg: Any) -> bool:
    """faster-whisper dual gate: drop a segment only when it is BOTH likely non-speech
    (`no_speech_prob > 0.6`) AND low-confidence (`avg_logprob < -1.0`). True = keep."""
    if no_speech_prob is None or avg_logprob is None:
        return True
    return not (
        no_speech_prob > float(getattr(cfg, "no_speech_threshold", 0.6))
        and avg_logprob < float(getattr(cfg, "logprob_threshold", -1.0))
    )
