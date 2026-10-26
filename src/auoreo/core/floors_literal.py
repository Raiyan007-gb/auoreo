"""Literal secrets in shell commands (spec §25.10 "argv scanner pre-permission").

A command that carries a credential inline (`curl -H "Authorization: Bearer sk-…"`,
`OPENAI_API_KEY=sk-… python x.py`, a value copied out of `secrets.db`) leaks it into the
transcript, the audit log, shell history and process listings. Such a call always asks — never
auto-allowed, not even under yolo, a PermissionRequest hook or the smart-mode guardian — with
the span masked (`head6…tail4`) on the approval card and the hint below. Non-interactive runs
deny it.

Two detectors: exact values of known secrets (the secret store, ≥ `literal_secret_min_len`
chars) and high-confidence key shapes (vendor prefixes, PEM private keys, credentialed URLs,
`Authorization: Bearer <literal>`, `<NAME>_KEY|TOKEN|SECRET|PASSWORD=<literal>`). A reference
(`$OPENAI_API_KEY`, `${TOKEN}`) is fine — that is the fix.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

HINT = (
    "the command contains a literal secret; move it to secrets.db (`auoreo secrets set NAME`) "
    "and reference it via env (`$NAME`, declared in the tool's requires_env)"
)
NOTE = "literal secret in command"
_STRONG = (
    "sk-ant-",
    "sk-or-",
    "sk-proj-",
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
    "xapp-",
    "pplx-",
    "gsk_",
    "tvly-",
    "hf_",
    "xai-",
    "dsk-",
)
_PREFIX_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:" + "|".join(re.escape(p) for p in _STRONG) + r")[A-Za-z0-9_-]{16,}"
    r"|(?<![A-Za-z0-9_-])sk-[A-Za-z0-9]{32,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bAIza[0-9A-Za-z_-]{35}\b"
    r"|(?<![A-Za-z0-9])\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"
)
_PEM_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_DSN_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@$]+:(?P<v>[^/\s@$]{4,})@")
_BEARER_RE = re.compile(
    r"(?i)\b(?:authorization:\s*(?:bearer|token|basic)|x-api-key:)\s*(?P<v>[^\s'\"$]{12,})"
)
_ASSIGN_RE = re.compile(
    r"(?<![A-Za-z0-9_])[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)\s*=\s*(['\"]?)(?P<v>[^\s'\"$`]{12,})\1"
)


@dataclass(frozen=True)
class LiteralSecret:
    start: int
    end: int
    kind: str
    """`known` (a stored secret's value) or `pattern` (a key shape)."""


def mask_value(value: str) -> str:
    return "***" if len(value) < 14 else f"{value[:6]}…{value[-4:]}"


def find(text: str, known: Iterable[str] = (), *, min_len: int = 12) -> list[LiteralSecret]:
    """Every literal-secret span in `text` (sorted, non-overlapping)."""
    spans: list[LiteralSecret] = []
    for value in sorted({k for k in known if k and len(k) >= min_len}, key=len, reverse=True):
        start = text.find(value)
        while start >= 0:
            spans.append(LiteralSecret(start, start + len(value), "known"))
            start = text.find(value, start + len(value))
    for m in _PREFIX_RE.finditer(text):
        spans.append(LiteralSecret(m.start(), m.end(), "pattern"))
    for rx in (_DSN_RE, _BEARER_RE, _ASSIGN_RE):
        for m in rx.finditer(text):
            spans.append(LiteralSecret(m.start("v"), m.end("v"), "pattern"))
    for m in _PEM_RE.finditer(text):
        spans.append(LiteralSecret(m.start(), m.end(), "pattern"))
    out: list[LiteralSecret] = []
    for s in sorted(spans, key=lambda s: (s.start, -(s.end - s.start))):
        if out and s.start < out[-1].end:
            continue
        out.append(s)
    return out


def masked(text: str, spans: list[LiteralSecret]) -> str:
    """`text` with each span replaced by its `head6…tail4` mask."""
    out, at = [], 0
    for s in spans:
        out += [text[at : s.start], mask_value(text[s.start : s.end])]
        at = s.end
    return "".join(out) + text[at:]
