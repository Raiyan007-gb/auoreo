"""Starlark exec policies (spec §2.10): `policies/<name>.star` run after floors, before rules.

Each policy is a Starlark module (starlark-rust via `starlark-pyo3`: no `while`, no
imports/`load()`, no IO, deterministic) that defines `decide(req)`. `req` is a dict with
`tool, permission, pattern, mode, interactive, agent, session_id, cwd`; `decide` returns
`"allow" | "deny" | "ask" | "review"` or `None` (no opinion).

`PolicyEngine.decide()` asks enabled policies in priority order (high first, then name);
the first non-None answer wins. It fails closed:

- a policy that does not compile (or a `policies/*.star` the loader rejected) answers
  `deny` for every request (`policy_compile_error:<name>`), ahead of every other policy;
- a runtime error, a wrong return value or a call over the 50 ms budget answers `review`
  (`policy_error:<name>`), never `allow`.

Helpers available to policies besides the Starlark builtins: `re_match(pattern, s)`
(Python `re.search`, bool) and `glob_match(pattern, s)` (fnmatch).
"""

from __future__ import annotations

import fnmatch
import re
import time
import weakref
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

import starlark as sl

if TYPE_CHECKING:
    from auoreo.harness.registry import Harness, Snapshot

PolicyAction = Literal["allow", "deny", "ask", "review"]
ACTIONS: frozenset[str] = frozenset({"allow", "deny", "ask", "review"})
BUDGET_S = 0.05
MAX_CALLSTACK = 64
REQ_FIELDS = ("tool", "permission", "pattern", "mode", "interactive", "agent", "session_id", "cwd")
_HELPER_INPUT_CAP = 20_000


class PolicyCompileError(ValueError):
    pass


@dataclass(frozen=True)
class PolicyResult:
    action: PolicyAction | None
    policy: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class CompiledPolicy:
    name: str
    priority: int
    source: str
    decide: Callable[[Mapping[str, Any]], object]
    broken: str | None = None
    """Compile error: the policy then denies every request (fail closed)."""


# ------------------------------------------------------------------ helpers (pure)
@lru_cache(maxsize=256)
def _regex(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def _re_match(pattern: str, value: str) -> bool:
    return _regex(str(pattern)).search(str(value)[:_HELPER_INPUT_CAP]) is not None


def _glob_match(pattern: str, value: str) -> bool:
    return fnmatch.fnmatchcase(str(value)[:_HELPER_INPUT_CAP], str(pattern))


_HELPERS: dict[str, Callable[..., object]] = {"re_match": _re_match, "glob_match": _glob_match}


def _dialect() -> sl.Dialect:
    d = sl.Dialect.standard()
    d.enable_load = False
    return d


def _deadline_options(budget_s: float) -> sl.EvalOptions:
    deadline = time.monotonic() + budget_s
    return sl.EvalOptions(
        check_cancelled=lambda: time.monotonic() > deadline, max_callstack_size=MAX_CALLSTACK
    )


# ------------------------------------------------------------------ compile
def compile_source(name: str, text: str, filename: str = "<policy>") -> Callable[[Mapping[str, Any]], object]:
    """Parse + evaluate a policy module and return its `decide` as a Python callable.
    Raises `PolicyCompileError` on any problem."""
    try:
        ast = sl.parse(filename, text, _dialect())
        mod = sl.Module()
        for helper, fn in _HELPERS.items():
            mod.add_callable(helper, fn)
        glb = sl.Globals.standard()
        sl.eval_with(_deadline_options(BUDGET_S * 4), mod, ast, glb)
        probe = sl.parse("<probe>", 'type(decide) == "function"', _dialect())
        is_fn = sl.eval(mod, probe, glb)
        frozen = mod.freeze()
    except sl.StarlarkError as exc:
        raise PolicyCompileError(_first_line(exc)) from exc
    except Exception as exc:  # e.g. a helper raising at top level
        raise PolicyCompileError(f"{type(exc).__name__}: {exc}"[:300]) from exc
    if is_fn is not True:
        raise PolicyCompileError(f"policy {name} does not define a function decide(req)")

    def decide(req: Mapping[str, Any]) -> object:
        return frozen.call_with(_deadline_options(BUDGET_S), "decide", dict(req)).value

    return decide


def check_policy(path: Path, name: str, text: str) -> None:
    """Loader seam: a policy that does not compile is a `LoadError` (shown by
    `harness validate` and doctor; `compile_policies` then denies everything for it)."""
    from auoreo.harness.loader import LoadError

    if not re.search(r"^def decide\(", text, re.MULTILINE):
        raise LoadError(path, "policy must define `def decide(req):`")
    try:
        compile_source(name, text, str(path))
    except PolicyCompileError as exc:
        raise LoadError(path, f"policy does not compile: {exc}") from exc


def _first_line(exc: Exception) -> str:
    lines = [ln.strip() for ln in str(exc).splitlines() if ln.strip()]
    text = " ".join(lines[:2]) if lines else type(exc).__name__
    return text.removeprefix("error: ")[:300]


def _broken(name: str, priority: int, source: str, why: str) -> CompiledPolicy:
    def deny_all(req: Mapping[str, Any]) -> object:
        return "deny"

    return CompiledPolicy(name, priority, source, deny_all, broken=why)


def compile_policies(harness: Harness) -> tuple[list[CompiledPolicy], list[str]]:
    """Compile every enabled policy; loader-rejected and uncompilable policies become
    deny-all entries. Returns `(policies, error strings)`."""
    out: list[CompiledPolicy] = []
    errors: list[str] = []
    for name, loaded in sorted(harness.policies.items()):
        m = loaded.manifest
        if not m.enabled:
            continue
        try:
            text = Path(loaded.path).read_text(encoding="utf-8")
            fn = compile_source(name, text, str(loaded.path))
        except (OSError, PolicyCompileError) as exc:
            errors.append(f"policy {name}: {exc}")
            out.append(_broken(name, m.priority, loaded.source, str(exc)))
            continue
        out.append(CompiledPolicy(name, m.priority, loaded.source, fn))
    for err in harness.errors:  # rejected by the loader (bad header, does not compile)
        path = Path(err.path)
        if path.suffix == ".star" and path.parent.name == "policies":
            errors.append(f"policy {path.stem}: {err.message}")
            out.append(_broken(path.stem, 1000, "error", err.message))
    return out, errors


# ------------------------------------------------------------------ evaluate
class PolicyEngine:
    def __init__(self, policies: list[CompiledPolicy], errors: list[str] | None = None) -> None:
        # broken policies first (fail closed), then priority high → low, then name
        self.policies = sorted(policies, key=lambda p: (p.broken is None, -p.priority, p.name))
        self.errors = list(errors or [])

    def decide(self, req: Mapping[str, Any]) -> PolicyResult:
        frozen = MappingProxyType({k: req.get(k) for k in REQ_FIELDS})
        for p in self.policies:
            if p.broken is not None:
                return PolicyResult("deny", p.name, f"policy_compile_error:{p.name}")
            t0 = time.monotonic()
            try:
                answer = p.decide(frozen)
            except Exception:
                return PolicyResult("review", p.name, f"policy_error:{p.name}")
            if time.monotonic() - t0 > BUDGET_S:
                return PolicyResult("review", p.name, f"policy_error:{p.name}")
            if answer is None:
                continue
            if not isinstance(answer, str) or answer not in ACTIONS:
                return PolicyResult("review", p.name, f"policy_error:{p.name}")
            return PolicyResult(answer, p.name, f"policy:{p.name}")  # type: ignore[arg-type]
        return PolicyResult(None)


_CACHE: dict[int, tuple[weakref.ref[Snapshot], PolicyEngine]] = {}


def policy_engine(harness: Harness) -> PolicyEngine:
    """The compiled engine for the harness's current snapshot (recompiled after a reload)."""
    snap = harness.snapshot
    hit = _CACHE.get(id(harness))
    if hit is not None and hit[0]() is snap:
        return hit[1]
    engine = PolicyEngine(*compile_policies(harness))
    for key in [k for k, (ref, _) in _CACHE.items() if ref() is None]:
        del _CACHE[key]
    _CACHE[id(harness)] = (weakref.ref(snap), engine)
    return engine
