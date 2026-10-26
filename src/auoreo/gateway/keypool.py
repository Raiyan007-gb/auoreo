"""Multikey pool per provider (spec §25.9 L1009). No `litellm` here.

Secrets named `provider/<name>/<slot>` (e.g. `provider/openrouter/work`,
`provider/openrouter/personal`) form the pool of provider `<name>`. `select()` picks a slot
(round-robin, or least-recently-failed), skipping slots in cooldown; a slot that answers
401/429 is put in a jittered cooldown with `fail()` and `rotate()` moves to the next one.
An OAuth slot may register a refresh hook that is tried once before the slot is rotated
away. Slot NAMES are safe to log/audit; values never leave this module except as the
`api_key` of the request that uses them.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)
PREFIX = "provider/"
RefreshHook = Callable[[str], str | None]
"""`(slot name) → fresh secret value` (OAuth refresh) or None when it cannot refresh."""


def slot_name(provider: str, slot: str) -> str:
    return f"{PREFIX}{provider}/{slot}"


def parse_slot(name: str) -> tuple[str, str] | None:
    """`provider/openrouter/work` → `("openrouter", "work")`; anything else → None."""
    if not name.startswith(PREFIX) or "#" in name:
        return None
    parts = name[len(PREFIX) :].split("/")
    if len(parts) != 2 or not all(parts):
        return None
    return parts[0], parts[1]


@dataclass
class _Slot:
    name: str
    cooldown_until: float = 0.0
    last_failed: float = 0.0
    refreshed: bool = False


@dataclass
class KeyPool:
    """Slots of ONE provider. `names`/`secret` read the secret store lazily on `reload()`."""

    provider: str
    names: Callable[[], list[str]]
    secret: Callable[[str], str | None]
    strategy: str = "round_robin"
    cooldown_s: float = 5.0
    clock: Callable[[], float] = time.monotonic
    refresh_hooks: dict[str, RefreshHook] = field(default_factory=dict)
    _slots: list[_Slot] = field(default_factory=list)
    _rr: int = 0
    current: str | None = None

    def __post_init__(self) -> None:
        self.reload()

    def reload(self) -> None:
        """Re-read slot names (after a secrets write); cooldown state of kept slots survives."""
        old = {s.name: s for s in self._slots}
        found = sorted(n for n in self.names() if (p := parse_slot(n)) and p[0] == self.provider)
        self._slots = [old.get(n) or _Slot(n) for n in found]

    def has_credentials(self) -> bool:
        return any(self.secret(s.name) for s in self._slots)

    def __len__(self) -> int:
        return len(self._slots)

    def _ready(self) -> list[_Slot]:
        now = self.clock()
        return [s for s in self._slots if s.cooldown_until <= now and self.secret(s.name)]

    def peek(self) -> str | None:
        """The slot `select()` would pick next, without advancing round-robin."""
        ready = self._ready()
        if not ready:
            return None
        if self.strategy == "least_recently_failed":
            return min(ready, key=lambda s: (s.last_failed, s.name)).name
        return ready[self._rr % len(ready)].name

    def select(self) -> tuple[str, str] | None:
        """`(slot name, value)` for the next request; None when every slot is cooling down."""
        ready = self._ready()
        if not ready:
            return None
        if self.strategy == "least_recently_failed":
            slot = min(ready, key=lambda s: (s.last_failed, s.name))
        else:
            slot = ready[self._rr % len(ready)]
            self._rr += 1
        value = self.secret(slot.name)
        if value is None:
            return None
        self.current = slot.name
        return slot.name, value

    def fail(self, name: str, status: int | None = None) -> None:
        """Cool `name` down (±20 % jitter so rotated slots do not stampede back together)."""
        for s in self._slots:
            if s.name == name:
                now = self.clock()
                s.last_failed = now
                s.cooldown_until = now + self.cooldown_s * random.uniform(0.8, 1.2)
                log.info("key pool %s: slot %s cooling down after %s", self.provider, name, status)

    def refresh_once(self, name: str) -> str | None:
        """OAuth slots: run the refresh hook at most once per slot until it next succeeds."""
        hook = self.refresh_hooks.get(name)
        slot = next((s for s in self._slots if s.name == name), None)
        if hook is None or slot is None or slot.refreshed:
            return None
        slot.refreshed = True
        try:
            return hook(name)
        except Exception:  # a broken refresh must never break the request path
            log.warning("key pool %s: refresh hook for %s failed", self.provider, name)
            return None

    def succeeded(self, name: str) -> None:
        for s in self._slots:
            if s.name == name:
                s.refreshed = False

    def rotate(self, failed: str, status: int | None = None) -> tuple[str, str] | None:
        """Cool `failed` down and select the next ready slot (None when nothing is left)."""
        self.fail(failed, status)
        names = [s.name for s in self._slots]
        ready = {s.name for s in self._ready()}
        start = names.index(failed) + 1 if failed in names else 0
        for name in names[start:] + names[:start]:  # the slot after the failed one, wrapping
            if name in ready and name != failed and (value := self.secret(name)):
                self.current = name
                return name, value
        return None


def pools_from(
    names: Callable[[], list[str]],
    secret: Callable[[str], str | None],
    strategy: str = "round_robin",
    cooldown_s: float = 5.0,
) -> dict[str, KeyPool]:
    """One `KeyPool` per provider that has at least one `provider/<name>/<slot>` secret."""
    providers = sorted({p[0] for n in names() if (p := parse_slot(n))})
    return {
        prov: KeyPool(prov, names, secret, strategy=strategy, cooldown_s=cooldown_s) for prov in providers
    }


__all__ = ["KeyPool", "PREFIX", "RefreshHook", "parse_slot", "pools_from", "slot_name"]
