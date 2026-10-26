"""Secret redaction (on by default, snapshot at import). Spec §19.10.

Two output modes:
- `mask` (display): `head6…tail4` for long values, `***` for short ones.
- `sentinel` (file reads): `«redacted:<label>…»` — non-reusable and carries no entropy, so a
  model that writes the redacted text back cannot corrupt a real secret (see
  `auoreo.tools.writeguard`).

Split secrets (§19.10, I1): zero-width characters (U+200B–U+200D, U+2060, U+FEFF) and C0
controls other than tab/newline/CR (plus DEL) are stripped before pattern matching; the
changed spans are mapped back onto the original text, so everything outside a secret keeps
its invisible characters and the secret span is masked like any other.
"""

from __future__ import annotations

import difflib
import logging
import os
import re
from collections.abc import Callable, Iterable
from typing import Any, Literal

RedactMode = Literal["mask", "sentinel", "off"]

# Prefixes: each starts with >=2 literal chars so the substring pre-screen is exact.
PREFIXES: tuple[str, ...] = (
    "sk-or-",
    "sk-ant-",
    "sk-proj-",
    "sk-",
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
    "github_pat_",
    "glpat-",
    "xoxb-",
    "xoxp-",
    "xoxa-",
    "xoxs-",
    "xoxr-",
    "xapp-",
    "AIza",
    "pplx-",
    "exa_",
    "gsk_",
    "tvly-",
    "hf_",
    "AKIA",
    "SG.",
    "xai-",
    "fw-",
    "fpk_",
    "dsk-",
    "gAAAA",  # Fernet tokens
    "eyJ",  # JWT
)
# Short generic prefixes that also start ordinary hyphenated words: the token must hold a digit.
WEAK_PREFIXES: tuple[str, ...] = ("llm-", "r1-", "oai-")
ALL_PREFIXES: tuple[str, ...] = PREFIXES + WEAK_PREFIXES

_TOKEN = r"[A-Za-z0-9_\-.=]{10,}"
_PREFIX_RE = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(p) for p in PREFIXES) + r")" + _TOKEN)
_WEAK_RE = re.compile(
    r"(?<![A-Za-z0-9])(" + "|".join(re.escape(p) for p in WEAK_PREFIXES) + r")"
    r"(?=[A-Za-z_\-]*\d)[A-Za-z0-9_\-]{10,}"
)
_SECRET_WORDS = r"(?:API_KEY|SECRET|TOKEN|PASSWORD|PASSWD)"
_FRESH = r"(?![^\s'\"]*[…«*])"  # skip values an earlier pass already redacted
_ASSIGN_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*" + _SECRET_WORDS + r"[A-Z0-9_]*)\s*[=:]\s*(['\"]?)" + _FRESH + r"([^\s'\"]{6,})\2"
)
# Upper-case env names ending in `_KEY` (`FAL_KEY=...`); case-sensitive so `sort_key=` is left alone.
_ENV_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9_]*_KEY)\s*=\s*(['\"]?)" + _FRESH + r"([^\s'\"]{6,})\2")
_KEYWORDS = r"(?:api[_-]?key|apikey|secret|token|password|passwd|credentials?|private[_-]?key|access[_-]?key)"
_JSON_RE = re.compile(r'(?i)("[^"\n]*' + _KEYWORDS + r'[^"\n]*"\s*:\s*")' + _FRESH + r'([^"\n]{6,})(")')
_YAML_RE = re.compile(
    r"(?im)^(\s*-?\s*[A-Za-z0-9_.-]*"
    + _KEYWORDS
    + r"[A-Za-z0-9_.-]*\s*:[ \t]+)(['\"]?)"
    + _FRESH
    + r"([^\s'\"#]{6,})\2"
)
_AUTH_RE = re.compile(r"(?i)\b(authorization|x-api-key)(\s*[:=]\s*)(bearer\s+)?" + _FRESH + r"([^\s,;]{8,})")
_TELEGRAM_RE = re.compile(r"(?<![A-Za-z0-9])(\d{8,10}):([A-Za-z0-9_-]{35})(?![A-Za-z0-9_-])")
_PEM_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)
_DSN_RE = re.compile(r"\b([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^/\s@]+)@")
_PHONE_RE = re.compile(r"(?<![\w+])\+?\d{1,3}[ .-]?\(?\d{2,4}\)?[ .-]\d{3,4}[ .-]\d{3,4}(?![\w])")

ENABLED: bool = os.environ.get("AUOREO_REDACT_SECRETS", "1").lower() not in {"0", "false", "no"}
PHONES: bool = os.environ.get("AUOREO_REDACT_PHONES", "").lower() in {"1", "true", "yes"}
SENTINEL_OPEN = "«redacted:"


def configure(*, phones: bool | None = None) -> None:
    """Startup snapshot of opt-in passes (called once from `configure_logging`)."""
    global PHONES
    if phones is not None:
        PHONES = phones


def mask(value: str) -> str:
    return "***" if len(value) < 18 else f"{value[:6]}…{value[-4:]}"


def sentinel(label: str) -> str:
    return f"{SENTINEL_OPEN}{label}…»"


def _label_for(token: str) -> str:
    for p in ALL_PREFIXES:
        if token.startswith(p):
            return p
    return "secret"


def _hide(value: str, mode: RedactMode, label: str | None = None) -> str:
    if mode == "sentinel":
        return sentinel(label or _label_for(value))
    return mask(value)


# ---------------------------------------------------------------- extra patterns
_EXTRA: dict[str, tuple[re.Pattern[str], ...]] = {}
_NESTED_QUANT = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)[+*{]")


def _validate(rx: str) -> re.Pattern[str]:
    compiled = re.compile(rx)
    if compiled.match(""):
        raise ValueError(f"redaction pattern {rx!r} matches the empty string")
    if _NESTED_QUANT.search(rx):
        raise ValueError(f"redaction pattern {rx!r} has a nested quantifier (ReDoS risk)")
    depth, branch_start, i = 0, 0, 0
    while i < len(rx):  # a top-level alternative must not be empty or start with a wildcard
        ch = rx[i]
        if ch == "\\":
            i += 2
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "|" and depth == 0:
            if i == branch_start or rx[branch_start] in ".*+?":
                raise ValueError(f"redaction pattern {rx!r} has an empty/wildcard top-level branch")
            branch_start = i + 1
        i += 1
    if branch_start >= len(rx) or rx[branch_start] in ".*+?":
        raise ValueError(f"redaction pattern {rx!r} has an empty/wildcard top-level branch")
    return compiled


def register_redaction_patterns(name: str, regexes: Iterable[str]) -> None:
    """Additive-only registration for plugins/backends; matches are always redacted.

    Raises `ValueError` for a reused name or a pattern that fails the ReDoS/top-alt checks."""
    if name in _EXTRA:
        raise ValueError(f"redaction patterns '{name}' are already registered (additive only)")
    _EXTRA[name] = tuple(_validate(rx) for rx in regexes)


_LITERALS: dict[str, dict[str, str]] = {}
"""Exact secret values (e.g. secret custom variables) by set → {label: value}; replaced verbatim."""


def set_literals(name: str, values: dict[str, str]) -> None:
    """Replace the literal set `name` (values shorter than 6 chars are skipped: too ambiguous)."""
    _LITERALS[name] = {k: v for k, v in values.items() if len(v) >= 6}


def registered_patterns() -> dict[str, int]:
    return {k: len(v) for k, v in _EXTRA.items()}


REDACTION_VERSION = "1"
_STRUCTURAL_PASSES = 10  # PEM, telegram, JSON, YAML, assign, ENV key, auth, DSN, weak, phone


def pattern_count() -> int:
    """Prefixes + structural passes + registered extras (the debug-bundle manifest, §24.9)."""
    return len(ALL_PREFIXES) + _STRUCTURAL_PASSES + sum(len(v) for v in _EXTRA.values())


# ---------------------------------------------------------------------- redact
def _assign(mode: RedactMode) -> Callable[[re.Match[str]], str]:
    def sub(m: re.Match[str]) -> str:
        return f"{m.group(1)}={m.group(2)}{_hide(m.group(3), mode, m.group(1))}{m.group(2)}"

    return sub


_INVISIBLE = re.compile("[\u200b-\u200d\u2060\ufeff\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def redact(text: str, mode: RedactMode = "mask", *, force: bool = False) -> str:
    """`force=True` (debug bundles, §24.9) redacts even when `AUOREO_REDACT_SECRETS` is off."""
    if not (ENABLED or force) or not text or mode == "off":
        return text
    if _INVISIBLE.search(text):
        return _redact_split(text, mode)
    return _redact_core(text, mode)


def _redact_split(text: str, mode: RedactMode) -> str:
    """Strip invisible/control characters, redact, then map the changed spans back."""
    keep = [i for i, ch in enumerate(text) if not _INVISIBLE.match(ch)]
    stripped = "".join(text[i] for i in keep)
    redacted = _redact_core(stripped, mode)
    if redacted == stripped:
        return _redact_core(text, mode)
    out: list[str] = []
    pos = 0  # next original index not yet emitted
    ops = difflib.SequenceMatcher(None, stripped, redacted, autojunk=False).get_opcodes()
    for tag, i1, i2, j1, j2 in ops:
        start = keep[i1] if i1 < len(keep) else len(text)
        end = keep[i2 - 1] + 1 if i2 > i1 else start
        if tag == "equal":
            end = keep[i2 - 1] + 1
            out.append(text[pos:end])
        else:
            out.append(text[pos:start] + redacted[j1:j2])
        pos = max(pos, end)
    out.append(text[pos:])
    return "".join(out)


def _redact_core(text: str, mode: RedactMode) -> str:
    for literals in _LITERALS.values():  # exact values first, before other passes reshape them
        for label, value in literals.items():
            if value in text:
                text = text.replace(value, "***" if mode == "mask" else sentinel(label))
    if any(p in text for p in ALL_PREFIXES):
        text = _PREFIX_RE.sub(lambda m: _hide(m.group(0), mode), text)
        text = _WEAK_RE.sub(lambda m: _hide(m.group(0), mode), text)
    text = _PEM_RE.sub("«redacted:private-key»", text)
    text = _TELEGRAM_RE.sub(lambda m: f"{m.group(1)}:{_hide(m.group(2), mode, 'telegram')}", text)
    text = _JSON_RE.sub(lambda m: f"{m.group(1)}{_hide(m.group(2), mode)}{m.group(3)}", text)
    text = _YAML_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{_hide(m.group(3), mode)}{m.group(2)}", text)
    text = _ASSIGN_RE.sub(_assign(mode), text)
    text = _ENV_KEY_RE.sub(_assign(mode), text)
    text = _AUTH_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3) or ''}{_hide(m.group(4), mode, m.group(1))}", text
    )
    dsn_pw = "***" if mode == "mask" else sentinel("password")
    text = _DSN_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}:{dsn_pw}@", text)
    for patterns in _EXTRA.values():
        for rx in patterns:
            text = rx.sub(lambda m: _hide(m.group(0), mode, "custom"), text)
    if PHONES:
        text = _PHONE_RE.sub("***" if mode == "mask" else sentinel("phone"), text)
    return text


def redact_terminal_output(text: str, enabled: bool = True) -> str:
    """bash/exec output: redacted (display mask) unless `security.redact_terminal_output` is off."""
    return redact(text) if enabled else text


def redact_tool_output(text: str, *, mode: RedactMode, permission: str, settings: Any) -> str:
    """The executor's result pass: shell tools (permission `bash`: bash/exec/pwsh) follow
    `security.redact_terminal_output`; every other tool uses its manifest `redaction` mode."""
    if not getattr(settings, "redact_secrets", True):
        return text
    if permission == "bash":
        security = getattr(settings, "security", None)
        return redact_terminal_output(text, bool(getattr(security, "redact_terminal_output", True)))
    return redact(text, mode)


# A display mask (`head6…tail4` / `***`) right after a secret-ish key, or one that starts with a
# known provider prefix: what a model would copy back from a redacted view.
_MASK_MARKER = re.compile(
    r"(?:(?<![A-Za-z0-9])(?:" + "|".join(re.escape(p) for p in ALL_PREFIXES) + r")"
    r"[A-Za-z0-9_\-.=]*…[A-Za-z0-9_\-.=]{4}(?![A-Za-z0-9]))"
    r"|(?i:"
    + _KEYWORDS
    + r"[A-Za-z0-9_\-]*['\"]?\s*[=:]\s*['\"]?(?:[A-Za-z0-9_\-.=]{6}…[A-Za-z0-9_\-.=]{4}|\*\*\*))"
)


def contains_redaction_marker(text: str) -> str | None:
    """The first sentinel or display-mask marker in `text`, else None."""
    if (i := text.find(SENTINEL_OPEN)) >= 0:
        end = text.find("»", i)
        return text[i : end + 1 if end >= 0 else i + 40]
    m = _MASK_MARKER.search(text)
    return m.group(0) if m else None


def redact_obj(obj: Any, mode: RedactMode = "mask", *, force: bool = False) -> Any:
    if isinstance(obj, str):
        return redact(obj, mode, force=force)
    if isinstance(obj, dict):
        return {k: redact_obj(v, mode, force=force) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v, mode, force=force) for v in obj]
    return obj


class RedactingFormatter(logging.Formatter):
    """Redacts the fully formatted line; wraps another formatter when given one."""

    def __init__(self, *args: Any, inner: logging.Formatter | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.inner = inner

    def format(self, record: logging.LogRecord) -> str:
        text = self.inner.format(record) if self.inner is not None else super().format(record)
        return redact(text)


HOLD_BACK = 128
_TOKEN_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-.=")


class StreamRedactor:
    """Redact a text stream without ever splitting a secret across chunk boundaries.

    Chunk-wise `redact()` is wrong twice over: a prefix like `sk-` at the very start of a
    chunk loses its left context (`</task-notification>` chunked at `sk-notification` was
    masked), and a key spanning two chunks leaks its tail. This keeps the last `HOLD_BACK`
    characters (extended backwards to the start of a token run) unemitted until more text
    or `flush()` arrives, and redacts with one character of emitted context on the left.
    """

    def __init__(self, hold: int = HOLD_BACK) -> None:
        self.hold = hold
        self._pending = ""
        self._last = " "  # left context for the prefix lookbehind

    def feed(self, text: str) -> str:
        if not ENABLED:
            return text
        self._pending += text
        cut = len(self._pending) - self.hold
        if cut <= 0:
            return ""
        while cut > 0 and self._pending[cut - 1] in _TOKEN_CHARS and self._pending[cut] in _TOKEN_CHARS:
            cut -= 1  # never split a token run; the run is held until it ends or flush()
        if cut <= 0:
            return ""
        out, self._pending = self._pending[:cut], self._pending[cut:]
        return self._emit(out)

    def flush(self) -> str:
        out, self._pending = self._pending, ""
        return self._emit(out) if out else ""

    def _emit(self, out: str) -> str:
        safe = redact(self._last + out)[1:] if ENABLED else out
        self._last = out[-1] if out else self._last
        return safe
