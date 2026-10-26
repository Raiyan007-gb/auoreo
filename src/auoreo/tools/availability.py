"""Tool availability (spec §9.1/§9.2): `is_enabled`, `env_gate` over `requires_env`, and
the `check_fn` probe (TTL-cached). An unavailable tool is hidden from the model and listed
with its reason in `/api/harness`; the executor refuses it if called anyway.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
import os
import sys
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from auoreo.harness.loader import ToolDef

log = logging.getLogger(__name__)

HasSecret = Callable[[str], bool]


def _env_present(name: str) -> bool:
    return bool(os.environ.get(name))


def _load_probe(tool: ToolDef, ref: str) -> Callable[[], Any]:
    target, fn_name = ref.rsplit(":", 1)
    if target.endswith(".py"):
        if tool.path is None:
            raise ValueError("a file check_fn needs a tool folder")
        path = (tool.path / target).resolve()
        if tool.path.resolve() not in path.parents or not path.is_file():
            raise ValueError(f"check_fn file {target} is not inside the tool folder")
        mod_name = f"auoreo_toolcheck_{tool.name}"
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            raise ValueError(f"cannot import {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(target)
    fn = getattr(module, fn_name, None)
    if not callable(fn) or inspect.iscoroutinefunction(fn):
        raise ValueError(f"check_fn '{ref}' must be a sync callable")
    probe: Callable[[], Any] = fn
    return probe


class Availability:
    """Answers `reason(tool) -> str | None` (None = available). `check_fn` verdicts are
    cached per tool for `ttl` seconds; env/secret presence is checked every time (cheap)."""

    def __init__(self, ttl: float = 30.0, has_secret: HasSecret | None = None) -> None:
        self.ttl = ttl
        self.has_secret = has_secret
        self.clock: Callable[[], float] = time.monotonic
        self._cache: dict[tuple[str, str], tuple[float, str | None]] = {}
        self._fns: dict[tuple[str, str], Callable[[], Any]] = {}

    def invalidate(self) -> None:
        self._cache.clear()
        self._fns.clear()

    def _has(self, name: str) -> bool:
        if _env_present(name):
            return True
        try:
            return bool(self.has_secret and self.has_secret(name))
        except Exception:  # a broken secret backend never makes a tool appear
            return False

    def _env_reason(self, tool: ToolDef) -> str | None:
        m = tool.manifest
        if m.env_gate == "none" or not m.requires_env:
            return None
        missing = [n for n in m.requires_env if not self._has(n)]
        if m.env_gate == "all" and missing:
            return f"missing env/secret: {', '.join(missing)}"
        if m.env_gate == "any" and len(missing) == len(m.requires_env):
            return f"needs one of: {', '.join(m.requires_env)}"
        return None

    def _probe(self, tool: ToolDef, ref: str) -> str | None:
        key = (tool.name, f"{tool.path}:{ref}")
        now = self.clock()
        hit = self._cache.get(key)
        if hit is not None and now - hit[0] < self.ttl:
            return hit[1]
        try:
            fn = self._fns.get(key)
            if fn is None:
                fn = self._fns[key] = _load_probe(tool, ref)
            verdict = fn()
            reason = None if verdict is True or verdict is None else str(verdict or "check_fn: unavailable")
        except Exception as exc:
            log.info("check_fn of %s failed: %s", tool.name, exc)
            reason = f"check_fn failed: {type(exc).__name__}: {exc}"[:300]
        self._cache[key] = (now, reason)
        return reason

    def reason(self, tool: ToolDef) -> str | None:
        m = tool.manifest
        if not m.is_enabled:
            return "disabled (is_enabled: false)"
        if (why := self._env_reason(tool)) is not None:
            return why
        return self._probe(tool, m.check_fn) if m.check_fn else None
