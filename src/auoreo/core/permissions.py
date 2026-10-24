"""Permission algebra (spec §19.1–19.4).

`evaluate()` takes the LAST matching rule across ordered layers
(bundled → project → runtime → agent → session → approved). Floors run first and
can never be bypassed by any rule or mode. Modes then adjust an `ask` outcome.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from auoreo.harness.manifests import Rule

Action = Literal["allow", "ask", "deny"]
Mode = Literal["manual", "smart", "yolo", "plan", "acceptEdits"]

# Frozen at import: a prompt-injected runtime change cannot turn yolo on (§19.3).
YOLO_FROZEN: bool = os.environ.get("AUOREO_YOLO", "").lower() in {"1", "true", "yes"}

_HARDLINE: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"\brm\s+(-[a-z]*\s+)*-[a-z]*[rf][a-z]*\s+(-[a-z]*\s+)*(/|/\*|~|~/|\$HOME)(\s|$)",
        r"\bmkfs(\.\w+)?\b",
        r"\bdd\b[^|;&]*\bof=/dev/",
        r">\s*/dev/(sd|nvme|hd|disk)",
        r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
        r"\bkill\s+-9?\s*-1\b",
        r"\b(shutdown|reboot|halt|poweroff)\b",
        r"\binit\s+[06]\b",
        r"\bsystemctl\s+(poweroff|reboot|halt|kexec)\b",
        r"\bsudo\s+(-[a-zA-Z]*S|--stdin|-[a-zA-Z]*A)\b",
        r"\bchmod\s+(-R\s+)?[0-7]*777\s+/(\s|$)",
    )
)
_SECRET_PATH = re.compile(r"(^|/)(\.env(\.[^/]*)?|secrets\.db)$")
_COMPOUND = re.compile(r"[;&|`<>]|\$\(")
_FILE_PERMISSIONS = frozenset({"read", "edit"})


def _wild(pattern: str) -> re.Pattern[str]:
    """`*` matches anything; `\\*` is a literal star."""
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        out.append(".*" if ch == "*" else re.escape(ch))
        i += 1
    return re.compile("".join(out), re.S)


@lru_cache(maxsize=4096)
def _compiled(pattern: str) -> re.Pattern[str]:
    return _wild(pattern)


def matches(rule: Rule, permission: str, pattern: str) -> bool:
    return bool(_compiled(rule.permission).fullmatch(permission)) and bool(
        _compiled(rule.pattern).fullmatch(pattern)
    )


def is_compound(command: str) -> bool:
    """True if the command has unquoted shell operators (; | & ` $( < >)."""
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return True
    if any(t and set(t) <= set(";&|<>") for t in tokens):
        return True
    return "`" in command or "$(" in command


def _normalize(cmd: str) -> list[str]:
    base = cmd.strip()
    variants = {
        base,
        base.lower(),
        re.sub(r"\s+", " ", base),
        base.replace("\\", "").replace("'", "").replace('"', ""),
    }
    return [v for v in variants if v]


def _secret_target(value: str) -> bool:
    if value.endswith(".env.example"):
        return False
    return bool(_SECRET_PATH.search(value))


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str  # floor | rule | mode | default
    rule: Rule | None = None

    @property
    def detail(self) -> str:
        return f"{self.reason}:{self.rule.permission}:{self.rule.pattern}" if self.rule else self.reason


@dataclass
class PermissionEngine:
    rules: list[Rule]
    floors: list[str] = field(default_factory=list)
    mode: Mode = "manual"
    plans_dir: str | None = None
    session_rules: list[Rule] = field(default_factory=list)
    approved: list[Rule] = field(default_factory=list)  # in-memory `always` grants

    def __post_init__(self) -> None:
        if self.mode == "yolo" and not YOLO_FROZEN:
            # yolo is only honoured when AUOREO_YOLO was set at process start.
            self.mode = "manual"

    def floor(self, permission: str, pattern: str) -> str | None:
        """Return a reason if a floor blocks this call."""
        if permission in _FILE_PERMISSIONS and _secret_target(pattern):
            return "secret files are never readable or writable by tools"
        if permission == "bash":
            for variant in _normalize(pattern):
                if any(rx.search(variant) for rx in _HARDLINE):
                    return "blocked by hardline floor (destructive system command)"
                if any(fnmatch.fnmatchcase(variant, g) for g in self.floors):
                    return "blocked by a configured floor"
        return None

    def evaluate(self, permission: str, pattern: str, default: Action = "ask") -> Decision:
        if (why := self.floor(permission, pattern)) is not None:
            return Decision("deny", f"floor: {why}")
        compound = permission == "bash" and is_compound(pattern)
        found: Rule | None = None
        for rule in [*self.rules, *self.session_rules, *self.approved]:
            if not matches(rule, permission, pattern):
                continue
            if compound and rule.action == "allow" and rule.pattern != "*":
                continue  # compound commands never match a narrow allow rule
            found = rule
        decision = Decision(found.action, "rule", found) if found else Decision(default, "default")
        return self._apply_mode(decision, permission, pattern)

    def _apply_mode(self, d: Decision, permission: str, pattern: str) -> Decision:
        if self.mode == "plan" and permission == "edit":
            in_plans = self.plans_dir is not None and pattern.startswith(self.plans_dir)
            return d if in_plans else Decision("deny", "mode: plan mode blocks edits", d.rule)
        if d.action != "ask":
            return d
        if self.mode == "yolo":
            return Decision("allow", "mode: yolo", d.rule)
        if self.mode == "acceptEdits" and permission == "edit":
            return Decision("allow", "mode: acceptEdits", d.rule)
        return d

    def grant_always(self, permission: str, pattern: str) -> None:
        self.approved.append(Rule(permission=permission, pattern=pattern, action="allow"))
