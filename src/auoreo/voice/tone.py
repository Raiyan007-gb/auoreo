"""Tone classifier (F4, spec §2.1 L51 P1) and the per-turn style lines of the context block.

`classify(text, prosody=None)` is deterministic and dependency-free: multilingual-light
lexicons (en/de/fr/es/it/pt/nl + a few CJK cues) + punctuation (`!!`, `??`, `?!`) +
intensity (ALL-CAPS words, stretched letters, emoji) → `tone ∈ {neutral, frustrated, urgent,
confused, positive}` with a confidence in 0..1. Optional audio prosody (`prosody_of`: RMS
variance across 100 ms windows, speaking rate in words/s from STT segments) adds intensity.

`tone_of(text, settings)` renders what the runner passes as `{{tone}}` into the Tier-2
context block (never the stable system prompt):

    tone: frustrated (0.85)
    guidance: be concise, acknowledge the issue, give the next concrete step   ← P2, opt-in
    language: the user wrote in en; answer in de-DE (the configured locale)    ← locale set

The guidance line appears only with `tone_adaptive: true` and a frustrated/confused tone at
confidence ≥ 0.7 (`ADAPTIVE_MIN`). The language line appears only when `settings.locale` is
set and `detect_language` confidently finds a different input language.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

TONES = ("neutral", "frustrated", "urgent", "confused", "positive")
ADAPTIVE_MIN = 0.7
ADAPTIVE_TONES = frozenset({"frustrated", "confused"})
GUIDANCE = "be concise, acknowledge the issue, give the next concrete step"
NEUTRAL_BELOW = 1.0
"""A top score under this is neutral."""

LEXICON: dict[str, tuple[str, ...]] = {
    tone: tuple(p.strip() for p in phrases.split("|"))
    for tone, phrases in {
        "frustrated": (
            "broken|still not|doesn't work|does not work|not working|nothing works|annoying|annoyed|"
            "frustrat|ridiculous|useless|again?|wtf|damn|hate|terrible|awful|sick of|fed up|stupid|"
            "keeps failing|waste of|"
            "kaputt|funktioniert nicht|geht nicht|nervig|schon wieder|ärgerlich|"  # de
            "ne marche pas|ne fonctionne pas|marre|énervant|"  # fr
            "no funciona|harto|molesto|otra vez|inútil|"  # es
            "non funziona|fastidioso|di nuovo|não funciona|irritante|de novo|"  # it, pt
            "werkt niet|irritant|動かない|壊れ|イライラ|不行|坏了|烦"  # nl, ja, zh
        ),
        "urgent": (
            "urgent|asap|immediately|right now|right away|emergency|critical|hurry|deadline|"
            "production is down|prod is down|outage|as soon as possible|"
            "dringend|sofort|notfall|eilig|immédiatement|tout de suite|vite|"  # de, fr
            "urgente|inmediatamente|ahora mismo|rápido|subito|immediatamente|"  # es, it
            "imediatamente|agora mesmo|meteen|至急|緊急|急いで|紧急|马上|立刻"  # pt, nl, ja, zh
        ),
        "confused": (
            "confused|confusing|don't understand|dont understand|do not understand|don't get|"
            "what do you mean|makes no sense|not sure|unclear|lost|how come|why does|huh|"
            "i'm not following|"
            "verstehe nicht|verwirrt|unklar|keine ahnung|comprends pas|confus|pas clair|"  # de, fr
            "no entiendo|confundido|no está claro|non capisco|confuso|"  # es, it
            "não entendo|não entendi|snap het niet|begrijp het niet|"  # pt, nl
            "わからない|分からない|不明白|不懂"  # ja, zh
        ),
        "positive": (
            "thanks|thank you|great|awesome|perfect|excellent|love it|nice|works now|amazing|"
            "brilliant|well done|cool|appreciate|"
            "danke|super|toll|prima|klasse|perfekt|merci|génial|parfait|"  # de, fr
            "gracias|genial|perfecto|excelente|grazie|ottimo|perfetto|"  # es, it
            "obrigado|obrigada|ótimo|perfeito|bedankt|geweldig|"  # pt, nl
            "ありがとう|すごい|谢谢|太好了"  # ja, zh
        ),
    }.items()
}
EMOJI: dict[str, str] = {
    "😡": "frustrated", "🤬": "frustrated", "😤": "frustrated", "😠": "frustrated",
    "🤔": "confused", "😕": "confused", "😵": "confused",
    "🚨": "urgent", "⏰": "urgent", "🔥": "urgent",
    "😊": "positive", "🙂": "positive", "👍": "positive", "🎉": "positive", "❤": "positive", "🙏": "positive",
}  # fmt: skip
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_STRETCH = re.compile(r"([^\W\d_])\1{3,}", re.UNICODE)
_CJK = re.compile(r"[぀-ヿ㐀-鿿]")


@dataclass(frozen=True)
class Prosody:
    rms_var: float | None = None
    """Variance of normalised RMS over 100 ms windows (shouting / agitation raise it)."""
    words_per_s: float | None = None


@dataclass(frozen=True)
class Tone:
    label: str
    confidence: float
    scores: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"tone": self.label, "confidence": self.confidence}


def _hits(text: str, phrase: str) -> int:
    if _CJK.search(phrase):
        return text.count(phrase)
    return len(re.findall(r"(?<![^\W_])" + re.escape(phrase) + r"(?![^\W_])", text))


def _intensity(raw: str) -> float:
    words = _WORD.findall(raw)
    caps = sum(1 for w in words if len(w) >= 3 and w.isupper())
    score = 0.5 * min(raw.count("!"), 3) if "!!" in raw or raw.count("!") >= 2 else 0.0
    score += 0.5 * min(caps, 2) if caps and caps >= max(1, len(words) // 4) else 0.0
    score += 0.5 if _STRETCH.search(raw) else 0.0
    return score


def classify(text: str, prosody: Prosody | None = None) -> Tone:
    raw = text.strip()
    low = raw.lower()
    scores = dict.fromkeys(TONES[1:], 0.0)
    for tone, phrases in LEXICON.items():
        scores[tone] += sum(min(_hits(low, p), 2) for p in phrases)
    for ch, tone in EMOJI.items():
        scores[tone] += 1.0 * min(raw.count(ch), 2)
    q = raw.count("?")
    scores["confused"] += 0.75 if "??" in raw or "?!" in raw or "!?" in raw else 0.0
    scores["confused"] += 0.25 if q == 1 and scores["confused"] > 0 else 0.0
    boost = _intensity(raw)
    if prosody is not None:
        boost += 0.5 if (prosody.rms_var or 0.0) > 0.01 else 0.0
        scores["urgent"] += 0.5 if (prosody.words_per_s or 0.0) > 3.5 else 0.0
    emotional = max(("frustrated", "urgent", "positive"), key=lambda t: scores[t])
    if boost and scores[emotional] > 0:
        scores[emotional] += boost
    elif boost >= 1.0:  # shouting with no other cue reads as frustration
        scores["frustrated"] += boost
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], TONES.index(kv[0])))
    (top, s1), (_, s2) = ranked[0], ranked[1]
    rounded = {k: round(v, 2) for k, v in scores.items()}
    if s1 < NEUTRAL_BELOW:
        return Tone("neutral", round(0.9 - 0.3 * s1, 2), rounded)
    conf = min(0.95, 0.4 + 0.15 * s1) * (s1 / (s1 + s2))
    return Tone(top, round(max(0.3, conf), 2), rounded)


def prosody_of(wav: bytes, segments: Sequence[Any] = ()) -> Prosody:
    """RMS variance over 100 ms windows + words/s from STT segments (objects or dicts)."""
    from auoreo.voice.audio import read_wav, rms

    info = read_wav(wav)
    step = int(info.sample_rate * 0.1) * 2 * max(1, info.channels)
    levels = [rms(info.pcm[i : i + step]) for i in range(0, len(info.pcm) - step + 1, step)]
    var = None
    if len(levels) >= 2:
        mean = sum(levels) / len(levels)
        var = sum((v - mean) ** 2 for v in levels) / len(levels)
    words, spoken = 0, 0.0
    for s in segments:
        get = s.get if isinstance(s, dict) else lambda k, s=s: getattr(s, k, None)
        words += len(str(get("text") or "").split())
        spoken += max(0.0, float(get("end") or 0.0) - float(get("start") or 0.0))
    return Prosody(var, round(words / spoken, 2) if spoken > 0 else None)


# ------------------------------------------------------------------ language
_SCRIPTS: tuple[tuple[str, str], ...] = (
    ("ja", r"[぀-ヿ]"),
    ("ko", r"[가-힯]"),
    ("zh", r"[一-鿿]"),
    ("ru", r"[Ѐ-ӿ]"),
    ("ar", r"[؀-ۿ]"),
    ("he", r"[֐-׿]"),
    ("el", r"[Ͱ-Ͽ]"),
    ("hi", r"[ऀ-ॿ]"),
)
_STOPWORDS: dict[str, frozenset[str]] = {
    code: frozenset(words.split())
    for code, words in {
        "en": "the and is are you i it to of this that what with not please can",
        "de": "der die das und ist ich nicht ein eine du wie was mit bitte kann",
        "fr": "le la les et est je ne pas un une vous que avec pour merci",
        "es": "el la los las y es yo no un una que con por para gracias",
        "it": "il lo gli e è io non un una che con per sono grazie",
        "pt": "o os as e é eu não um uma que com para obrigado você",
        "nl": "de het een en is ik niet dat met voor je wat graag",
    }.items()
}


def detect_language(text: str) -> str | None:
    """Cheap guess: script first, then stopword counts (needs ≥ 2 hits and a clear winner)."""
    for code, rx in _SCRIPTS:
        if len(re.findall(rx, text)) >= 2:
            return code
    words = [w.lower() for w in _WORD.findall(text)]
    counts = {code: sum(1 for w in words if w in sw) for code, sw in _STOPWORDS.items()}
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    (best, n1), (_, n2) = ranked[0], ranked[1]
    return best if n1 >= 2 and n1 > n2 else None


# ------------------------------------------------------------------ context
def tone_of(text: str, settings: Any = None) -> str:
    """The `{{tone}}` value for the context block (empty when nothing applies)."""
    if settings is None:
        from auoreo.core.settings import get_settings

        settings = get_settings()
    lines: list[str] = []
    if getattr(settings, "tone_enabled", True):
        t = classify(text)
        lines.append(f"tone: {t.label} ({t.confidence:.2f})")
        adaptive = getattr(settings, "tone_adaptive", False)
        if adaptive and t.label in ADAPTIVE_TONES and t.confidence >= ADAPTIVE_MIN:
            lines.append(f"guidance: {GUIDANCE}")
    locale = getattr(settings, "locale", None)
    lang = detect_language(text) if locale else None
    if locale and lang and lang != str(locale).split("-")[0].split("_")[0].lower():
        lines.append(f"language: the user wrote in {lang}; answer in {locale} (the configured locale)")
    return "\n".join(lines)
