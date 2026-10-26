"""Permission algebra (spec §19.1–19.4, §19.6–19.7, §25.10).

`evaluate()` takes the LAST matching rule across ordered layers
(bundled → project → runtime → agent → session → approved). Floors run first and
can never be bypassed by any rule or mode. Modes then adjust an `ask` outcome; sensitive
write targets always end at `ask` (even under yolo/acceptEdits; floors still win).

Rules are sharded by permission name and precompiled (wildcard → regex + literal prefix);
a generation-keyed verdict cache answers repeated `(permission, pattern)` lookups.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
import shlex
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from auoreo.core import floors
from auoreo.harness.manifests import Rule

if TYPE_CHECKING:
    from auoreo.core.policy import PolicyEngine

log = logging.getLogger(__name__)
Action = Literal["allow", "ask", "deny"]
Mode = Literal["manual", "smart", "yolo", "plan", "acceptEdits"]
Reason = Literal["floor", "policy", "rule", "mode", "default", "session", "approved", "doom_loop"]
REASONS: tuple[str, ...] = ("floor", "policy", "rule", "mode", "default", "session", "approved", "doom_loop")

# Frozen at import: a prompt-injected runtime change cannot turn yolo on (§19.3).
YOLO_FROZEN: bool = os.environ.get("AUOREO_YOLO", "").lower() in {"1", "true", "yes"}

# `sudo` (§25.10): `-n` / `--non-interactive` never prompts; anything else may wait for a password.
_SUDO = re.compile(r"(^|[\s;&|(`])sudo(\s+(?P<flags>(-[a-zA-Z-]+\s*)*))?")
_SUDO_NONINTERACTIVE = re.compile(r"(^|\s)(-[a-zA-Z]*n[a-zA-Z]*|--non-interactive)(\s|$)")
_COMPOUND = re.compile(r"[;&|`<>]|\$\(")
_FILE_PERMISSIONS = frozenset({"read", "edit"})

MAX_PATTERN_LEN = 2048
SENSITIVE_NOTE = "sensitive write target always asks"
# Sensitive write targets (§19.7): always `ask`, even under yolo / acceptEdits.
SENSITIVE_WRITE_GLOBS: tuple[str, ...] = (
    "~/.ssh/*",
    "~/.bashrc",
    "~/.bash_profile",
    "~/.bash_login",
    "~/.profile",
    "~/.zshrc",
    "~/.zprofile",
    "~/.zshenv",
    "~/.netrc",
    "~/.pgpass",
    "~/.npmrc",
    "~/.pypirc",
    "~/.aws/*",
    "~/.config/gh/*",
    "~/.gnupg/*",
    "/etc/*",
    "*/.git/hooks/*",
    "$AUOREO_HOME/settings.json",
    "$AUOREO_HOME/.env",
    "$AUOREO_HOME/trusted_workspaces.json",  # an agent must never trust a workspace itself
    "$AUOREO_HOME/.db.key",
    "$AUOREO_HOME/.secrets.key",
)


def _wild(pattern: str) -> re.Pattern[str]:
    """`*` matches anything; `\\*` is a literal star. Runs of `*` collapse (no ReDoS)."""
    if len(pattern) > MAX_PATTERN_LEN:
        raise ValueError(f"permission pattern longer than {MAX_PATTERN_LEN} chars")
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        if ch == "*":
            if not out or out[-1] != ".*":
                out.append(".*")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("".join(out), re.S)


@lru_cache(maxsize=16384)
def _compiled(pattern: str) -> re.Pattern[str]:
    return _wild(pattern)


def _literal_prefix(pattern: str) -> str:
    """Literal characters before the first unescaped `*` (a cheap pre-filter)."""
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            out.append(pattern[i + 1])
            i += 2
            continue
        if ch == "*":
            break
        out.append(ch)
        i += 1
    return "".join(out)


def _esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace("*", "\\*")


def expand(pattern: str, cwd: str | None = None) -> str:
    """`~/`, `$HOME`, `$TMP`, `$AUOREO_HOME`, `$CWD` expansion (values are wildcard-escaped).
    A `$CWD` pattern without a known cwd is returned unchanged (and never matches a path)."""
    if "~" not in pattern and "$" not in pattern:
        return pattern
    from auoreo.core.paths import home_dir

    if pattern == "~" or pattern.startswith("~/"):
        pattern = "$HOME" + pattern[1:]
    values = {
        "$AUOREO_HOME": str(home_dir()),
        "$HOME": os.path.expanduser("~"),
        "$TMP": tempfile.gettempdir(),
    }
    if cwd:
        values["$CWD"] = cwd
    for key, value in values.items():
        pattern = pattern.replace(key, _esc(value.rstrip("/") or "/"))
    return pattern


def matches(rule: Rule, permission: str, pattern: str) -> bool:
    return bool(_compiled(rule.permission).fullmatch(permission)) and bool(
        _compiled(expand(rule.pattern)).fullmatch(pattern)
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
    return floors.is_secret_path(value)


def sudo_kind(command: str) -> Literal["bare", "noninteractive"] | None:
    """`bare` if any `sudo` in the command may prompt, `noninteractive` if every one is `-n`."""
    kinds = set()
    for variant in _normalize(command):
        for m in _SUDO.finditer(variant):
            flags = m.group("flags") or ""
            kinds.add("noninteractive" if _SUDO_NONINTERACTIVE.search(" " + flags) else "bare")
    if "bare" in kinds:
        return "bare"
    return "noninteractive" if kinds else None


def _names_sudo(pattern: str) -> bool:
    return pattern.lstrip().startswith("sudo")


def _explicit(hit: _Compiled | None, marker: str) -> bool:
    """An ask floor passes only on an `always` grant or an allow rule naming the command."""
    if hit is None or hit.rule.action != "allow":
        return False
    return hit.layer == "approved" or (bool(marker) and hit.prefix.startswith(marker))


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: Reason
    rule: Rule | None = None
    note: str = ""
    rule_id: str | None = None
    """`rules[i]` / `session[i]` / `approved[i]` (index in its layer) or `policy:<name>`."""

    @property
    def detail(self) -> str:
        if self.reason == "policy" and self.rule_id:
            return self.rule_id
        text = f"{self.reason}: {self.note}" if self.note else self.reason
        if self.rule is not None:
            text += f":{self.rule.permission}:{self.rule.pattern}"
        return text

    @property
    def sensitive(self) -> bool:
        return self.note == SENSITIVE_NOTE


@dataclass(frozen=True)
class _Compiled:
    index: int
    layer: Reason
    layer_index: int
    rule: Rule
    perm_re: re.Pattern[str]
    pat_re: re.Pattern[str]
    prefix: str
    narrow_allow: bool

    @property
    def rule_id(self) -> str:
        return f"{'rules' if self.layer == 'rule' else self.layer}[{self.layer_index}]"


class _HasManifest(Protocol):
    @property
    def manifest(self) -> Any: ...


def hidden_by_rules(permission: str, rules: Sequence[Rule]) -> bool:
    """§19.1 `disabled()`: hidden when the LAST rule naming this permission is `deny "*"`."""
    for rule in reversed(rules):
        if _compiled(rule.permission).fullmatch(permission):
            return rule.pattern == "*" and rule.action == "deny"
    return False


def visible_tools[T: _HasManifest](tools: Iterable[T], rules: Sequence[Rule]) -> list[T]:
    """Tools the model may see: drops those whose permission is wholly denied (pure)."""
    return [t for t in tools if not hidden_by_rules(t.manifest.permission_name, rules)]


@lru_cache(maxsize=8)
def _sensitive_res(home: str, auoreo_home: str) -> tuple[re.Pattern[str], ...]:
    return tuple(_wild(expand(g)) for g in SENSITIVE_WRITE_GLOBS)


def is_sensitive_write(path: str) -> bool:
    from auoreo.core.paths import home_dir

    res = _sensitive_res(os.path.expanduser("~"), str(home_dir()))
    return any(rx.fullmatch(path) for rx in res)


def path_within(path: str, root: str, cwd: str = ".") -> bool:
    """Real containment (`..`/symlinks resolved, separator-aware): `plansX/` is not in `plans/`."""
    p, r = os.path.realpath(os.path.join(cwd, os.path.expanduser(path))), os.path.realpath(root)
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def is_outside(path: str | Path, cwd: str | Path) -> bool:
    p, root = Path(path), Path(cwd)
    return p != root and root not in p.parents


_CACHE_MAX = 8192
_WATCHED = frozenset({"rules", "session_rules", "approved", "context"})
Shards = tuple[dict[str, list[_Compiled]], list[_Compiled], dict[str, list[_Compiled]]]


@dataclass
class PermissionEngine:
    rules: list[Rule]
    floors: list[str] = field(default_factory=list)
    mode: Mode = "manual"
    plans_dir: str | None = None
    session_rules: list[Rule] = field(default_factory=list)
    approved: list[Rule] = field(default_factory=list)  # in-memory `always` grants
    policies: PolicyEngine | None = None  # Starlark exec policies (after floors, before rules)
    context: dict[str, Any] = field(default_factory=dict)  # agent, session_id, cwd, tool → `req`

    def __setattr__(self, name: str, value: Any) -> None:
        object.__setattr__(self, name, value)
        if name in _WATCHED:
            self.bump()

    def __post_init__(self) -> None:
        if self.mode == "yolo" and not YOLO_FROZEN:
            # yolo is only honoured when AUOREO_YOLO was set at process start.
            self.mode = "manual"

    # ------------------------------------------------------------ shards + cache
    def bump(self) -> None:
        """Invalidate shards + verdict cache (rule, session-rule or grant change)."""
        object.__setattr__(self, "_generation", self.generation + 1)
        object.__setattr__(self, "_built", None)
        object.__setattr__(self, "_cache", {})

    @property
    def generation(self) -> int:
        return int(self.__dict__.get("_generation", 0))

    def _signature(self) -> tuple[int, int, int, int]:
        return (self.generation, len(self.rules), len(self.session_rules), len(self.approved))

    def _shards(self) -> Shards:
        built = self.__dict__.get("_built")
        sig = self._signature()
        if built is not None and built[0] == sig:
            return built[1]  # type: ignore[no-any-return]
        object.__setattr__(self, "_cache", {})  # in-place list mutation: drop stale verdicts
        cwd = self.context.get("cwd")
        shards: dict[str, list[_Compiled]] = {}
        wild: list[_Compiled] = []
        layers: tuple[tuple[Reason, list[Rule]], ...] = (
            ("rule", self.rules),
            ("session", self.session_rules),
            ("approved", self.approved),
        )
        index = 0
        for layer, rules in layers:
            for i, rule in enumerate(rules):
                pattern = expand(rule.pattern, str(cwd) if cwd else None)
                try:
                    c = _Compiled(
                        index,
                        layer,
                        i,
                        rule,
                        _compiled(rule.permission),
                        _compiled(pattern),
                        _literal_prefix(pattern),
                        rule.action == "allow" and rule.pattern != "*",
                    )
                except ValueError as exc:
                    log.warning("skipping permission rule %s: %s", rule, exc)
                    continue
                index += 1
                target = wild if "*" in rule.permission else shards.setdefault(rule.permission, [])
                target.append(c)
        value: Shards = (shards, wild, {})
        object.__setattr__(self, "_built", (sig, value))
        return value

    def _candidates(self, permission: str) -> list[_Compiled]:
        """Rules that may apply to `permission`, newest (highest index) first."""
        shards, wild, merged = self._shards()
        if (hit := merged.get(permission)) is not None:
            return hit
        own = shards.get(permission, [])
        extra = [c for c in wild if c.perm_re.fullmatch(permission)]
        out = sorted([*own, *extra], key=lambda c: c.index, reverse=True) if extra else own[::-1]
        merged[permission] = out
        return out

    def lookup(self, permission: str, pattern: str) -> _Compiled | None:
        """The last matching rule (compound bash never matches a narrow allow)."""
        cands = self._candidates(permission)
        cache: dict[tuple[str, str], _Compiled | None] = self.__dict__["_cache"]
        key = (permission, pattern)
        if key in cache:
            return cache[key]
        compound = permission == "bash" and is_compound(pattern)
        found: _Compiled | None = None
        for c in cands:
            if c.prefix and not pattern.startswith(c.prefix):
                continue
            if compound and c.narrow_allow:
                continue
            if c.pat_re.fullmatch(pattern):
                found = c
                break
        if len(cache) >= _CACHE_MAX:
            cache.clear()
        cache[key] = found
        return found

    # ------------------------------------------------------------------ floors
    def floor(self, permission: str, pattern: str, *, interactive: bool = True) -> str | None:
        """Return a reason if a floor blocks this call."""
        if permission in _FILE_PERMISSIONS and _secret_target(pattern):
            return "secret files are never readable or writable by tools"
        if permission == "bash":
            if (why := floors.hardline(pattern)) is not None:
                return f"blocked by {why}"
            for variant in _normalize(pattern):
                if any(rx.search(variant) for rx in floors.BACKSTOP):
                    return "blocked by hardline floor (destructive system command)"
                if any(fnmatch.fnmatchcase(variant, g) for g in self.floors):
                    return "blocked by a configured floor"
            if not interactive and sudo_kind(pattern) == "bare":
                return "bare sudo needs a human at a terminal; this run is non-interactive"
        return None

    def policy(
        self, permission: str, pattern: str, *, interactive: bool, context: Mapping[str, Any] | None
    ) -> Decision | None:
        """The exec-policy answer (`policy:<name>` / `policy_review:<name>` in `rule_id`), or None."""
        if self.policies is None:
            return None
        ctx = {**self.context, **(context or {})}
        req = {
            "tool": ctx.get("tool") or permission,
            "permission": permission,
            "pattern": pattern,
            "mode": self.mode,
            "interactive": interactive,
            "agent": ctx.get("agent"),
            "session_id": ctx.get("session_id"),
            "cwd": str(ctx["cwd"]) if ctx.get("cwd") is not None else None,
        }
        res = self.policies.decide(req)
        if res.action is None:
            return None
        if res.action == "review":
            return Decision("ask", "policy", rule_id=f"policy_review:{res.policy}")
        return Decision(res.action, "policy", rule_id=res.reason or f"policy:{res.policy}")

    def evaluate(
        self,
        permission: str,
        pattern: str,
        default: Action = "ask",
        *,
        interactive: bool = True,
        context: Mapping[str, Any] | None = None,
    ) -> Decision:
        if (why := self.floor(permission, pattern, interactive=interactive)) is not None:
            return Decision("deny", "floor", note=why)
        by_policy = self.policy(permission, pattern, interactive=interactive, context=context)
        if by_policy is not None and by_policy.action == "deny":
            return by_policy
        sudo = sudo_kind(pattern) if permission == "bash" else None
        hit = self.lookup(permission, pattern)
        found = hit.rule if hit is not None else None
        if hit is not None:
            decision = Decision(hit.rule.action, hit.layer, hit.rule, rule_id=hit.rule_id)
        else:
            decision = Decision(default, "default")
        if by_policy is not None:
            decision = by_policy  # a policy answer replaces the rules (but not the sudo floors)
        if sudo == "noninteractive":
            # `sudo -n` runs only on an explicit allow rule naming the sudo command (silent deny on miss).
            explicit = found is not None and found.action == "allow" and _names_sudo(found.pattern)
            return (
                decision
                if explicit
                else Decision("deny", "floor", note="sudo -n needs an explicit allow rule")
            )
        if by_policy is None or self.mode == "plan":  # yolo/acceptEdits never upgrade a policy ask
            decision = self._apply_mode(decision, permission, pattern)
        if sudo == "bare" and decision.action == "allow":
            return Decision("ask", "floor", decision.rule, note="bare sudo always asks")
        if permission == "bash" and decision.action == "allow":
            ask = floors.ask_floor(pattern)  # destructive-but-recoverable: asks even under yolo
            if ask is not None and not _explicit(hit, ask.marker):
                return Decision("ask", "floor", decision.rule, note=ask.reason, rule_id=decision.rule_id)
        if permission == "edit" and decision.action != "deny" and is_sensitive_write(pattern):
            return Decision("ask", "floor", decision.rule, note=SENSITIVE_NOTE, rule_id=decision.rule_id)
        return decision

    def external_directory(
        self, path: str | Path, cwd: str | Path, *, interactive: bool = True
    ) -> Decision | None:
        """§19.6: a file-tool path outside `cwd` is checked as permission `external_directory`
        (pattern = the path); None when the path is inside `cwd`."""
        if not is_outside(path, cwd):
            return None
        return self.evaluate("external_directory", str(path), "ask", interactive=interactive)

    def _apply_mode(self, d: Decision, permission: str, pattern: str) -> Decision:
        if self.mode == "plan" and permission == "edit":
            cwd = str(self.context.get("cwd") or ".")
            in_plans = self.plans_dir is not None and path_within(pattern, self.plans_dir, cwd)  # I1
            return d if in_plans else Decision("deny", "mode", d.rule, note="plan mode blocks edits")
        if d.action != "ask":
            return d
        if self.mode == "yolo":
            return Decision("allow", "mode", d.rule, note="yolo", rule_id=d.rule_id)
        if self.mode == "acceptEdits" and permission == "edit":
            return Decision("allow", "mode", d.rule, note="acceptEdits", rule_id=d.rule_id)
        return d

    def grant_always(self, permission: str, pattern: str) -> None:
        self.approved.append(Rule(permission=permission, pattern=pattern, action="allow"))
        self.bump()
