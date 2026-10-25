"""Delivery providers: where automation results go (log file, GitHub comment, Telegram, …).

Folder backends live in `backends/delivery/<name>/impl.py` (`Backend(settings)`); plugins
register process-wide providers at runtime with `register(name, provider)`. `resolve()`
looks in the dynamic registry first, then the folder backends (instances are cached).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from auoreo.backends import BackendStatus

if TYPE_CHECKING:
    from auoreo.core.services import Services
    from auoreo.core.settings import Settings


@dataclass(frozen=True)
class DeliveryReceipt:
    ok: bool
    detail: str = ""
    external_id: str | None = None


class DeliveryProvider(ABC):
    """`send(target, text, meta=…)` posts one result. `target` is provider-specific
    (`owner/repo#12` for GitHub comments, a chat id for Telegram, ignored by `log`)."""

    services: Services | None = None

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings

    def attach(self, services: Services) -> None:
        """Called by `resolve()` so a provider can emit events and read secrets."""
        self.services = services

    @abstractmethod
    async def send(self, target: str, text: str, *, meta: dict[str, Any]) -> DeliveryReceipt: ...

    async def check(self) -> BackendStatus:
        return BackendStatus(True, "ok")


DYNAMIC: dict[str, DeliveryProvider] = {}
_CACHE: dict[tuple[int, str], DeliveryProvider] = {}


def register(name: str, provider: DeliveryProvider) -> None:
    """Register (or replace) a process-wide provider; wins over a folder backend of the same name."""
    DYNAMIC[name] = provider


def unregister(name: str) -> None:
    DYNAMIC.pop(name, None)


def resolve(services: Services, name: str) -> DeliveryProvider:
    """Dynamic registry → `backends/delivery/<name>` (constructed once per Services)."""
    if name in DYNAMIC:
        provider = DYNAMIC[name]
        if provider.services is not services:
            provider.attach(services)
        return provider
    key = (id(services), name)
    if key in _CACHE:
        return _CACHE[key]
    defs = services.harness.backends_for("delivery")
    if name not in defs:
        known = sorted({*defs, *DYNAMIC})
        raise KeyError(f"unknown delivery provider '{name}' (known: {known})")
    inst = defs[name].load()(services.settings)
    if not isinstance(inst, DeliveryProvider):
        raise TypeError(f"backends/delivery/{name} does not implement DeliveryProvider")
    inst.attach(services)
    _CACHE[key] = inst
    return inst


def forget(services: Services) -> None:
    """Drop the cached folder-backend instances of one Services (called on shutdown)."""
    for key in [k for k in _CACHE if k[0] == id(services)]:
        _CACHE.pop(key, None)


def split_spec(spec: str) -> tuple[str, str]:
    """`github_comment:owner/repo#12` → (`github_comment`, `owner/repo#12`); `log` → (`log`, "")."""
    name, _, target = spec.partition(":")
    return name, target
