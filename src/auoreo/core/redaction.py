"""Secret redaction (on by default, snapshot at import). Spec §19.10."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

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
    "dsk-",
    "eyJ",
)
_TOKEN = r"[A-Za-z0-9_\-.=]{10,}"
_PREFIX_RE = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(p) for p in PREFIXES) + r")" + _TOKEN)
_ASSIGN_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:API_KEY|SECRET|TOKEN|PASSWORD|PASSWD)[A-Z0-9_]*)\s*[=:]\s*"
    r"(['\"]?)([^\s'\"]{6,})\2"
)
_AUTH_RE = re.compile(r"(?i)\b(authorization|x-api-key)(\s*[:=]\s*)(bearer\s+)?([^\s,;]{8,})")
_PEM_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)
_DSN_RE = re.compile(r"\b([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^/\s@]+)@")

ENABLED: bool = os.environ.get("AUOREO_REDACT_SECRETS", "1").lower() not in {"0", "false", "no"}


def mask(value: str) -> str:
    return "***" if len(value) < 18 else f"{value[:6]}…{value[-4:]}"


def redact(text: str) -> str:
    if not ENABLED or not text:
        return text
    if any(p in text for p in PREFIXES):
        text = _PREFIX_RE.sub(lambda m: mask(m.group(0)), text)
    text = _PEM_RE.sub("«redacted:private-key»", text)
    text = _ASSIGN_RE.sub(lambda m: f"{m.group(1)}={m.group(2)}{mask(m.group(3))}{m.group(2)}", text)
    text = _AUTH_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3) or ''}{mask(m.group(4))}", text)
    return _DSN_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}:***@", text)


def redact_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    return obj


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


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
