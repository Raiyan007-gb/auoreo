"""Sandbox startup probe (spec §19.8): never a silent downgrade.

`settings.backends.sandbox` unset (or `"auto"`) picks the strongest local backend that
probes OK: Linux `bwrap` (+ Landlock inside it), macOS `seatbelt`. Only when none is usable
does it run unconfined (`none`), with a warning, an audit row and a doctor warning — or a
refusal under `sandbox_policy = "fail"`. An explicit `backends.sandbox` is used as is.

Probes the selected `sandbox` backend once at startup. On failure, `sandbox_policy`
decides: `warn` logs, audits `sandbox.fallback` and installs the `none` backend; `fail`
refuses to start. The result is `services.ext["sandbox_boot"]`, and the chosen instance
is what `ctx.backend("sandbox")` returns.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from auoreo.backends import BackendStatus

if TYPE_CHECKING:
    from auoreo.backends.sandbox import SandboxBackend
    from auoreo.core.services import Services

log = logging.getLogger(__name__)
PROBE_TIMEOUT_S = 10.0
FALLBACK = "none"
AUTO = "auto"
AUTO_ORDER: dict[str, tuple[str, ...]] = {"linux": ("bwrap",), "darwin": ("seatbelt",)}


def auto_candidates(harness: Any) -> list[str]:
    """Confining backends worth probing on this platform (discovered + binaries present)."""
    known = harness.backends_for("sandbox")
    key = "linux" if sys.platform.startswith("linux") else sys.platform
    return [
        name
        for name in AUTO_ORDER.get(key, ())
        if name in known and all(shutil.which(b) for b in known[name].manifest.requires_binaries)
    ]


def configured(st: Any, harness: Any) -> str | None:
    """The backend name doctor/startup should use: explicit setting, else the first auto
    candidate (None = nothing confining is installed → the `none` default)."""
    chosen = st.backends.get("sandbox")
    if chosen not in (None, AUTO):
        return str(chosen)
    cands = auto_candidates(harness)
    return cands[0] if cands else None


class SandboxStartupRefused(SystemExit):  # noqa: N818 - a SystemExit, not an error to catch
    """`sandbox_policy = "fail"` and the selected backend failed its probe.

    A `SystemExit` so it passes through the extension loader (which isolates ordinary
    exceptions) and stops the server/CLI with the message."""


@dataclass
class SandboxBoot:
    backend: SandboxBackend
    selected: str
    status: BackendStatus
    fell_back: bool = False

    @property
    def enforced(self) -> bool:
        return any(self.backend.capabilities().values())

    def describe(self) -> dict[str, Any]:
        return {
            "selected": self.selected,
            "active": self.backend.name,
            "ok": self.status.ok,
            "detail": self.status.detail,
            "fell_back": self.fell_back,
            "enforced": self.enforced,
            **self.backend.capabilities(),
        }


async def probe(backend: Any) -> BackendStatus:
    try:
        status = await asyncio.wait_for(backend.check(), PROBE_TIMEOUT_S)
    except Exception as exc:  # a broken check() is a failed probe
        return BackendStatus(False, f"probe failed: {type(exc).__name__}: {exc}"[:300])
    return status if isinstance(status, BackendStatus) else BackendStatus(bool(status))


async def _auto(services: Services) -> SandboxBoot | None:
    """Probe the platform candidates in order; the first that passes is installed."""
    st = services.settings
    tried: list[str] = []
    for name in auto_candidates(services.harness):
        bdef = services.harness.select_backend("sandbox", name)
        try:
            assert bdef is not None
            backend = bdef.load()(st)
            status = await probe(backend)
        except Exception as exc:  # unloadable impl: try the next one
            status = BackendStatus(False, f"failed to load: {exc}"[:300])
        if status.ok:
            return _install(services, backend, name, status, False)
        tried.append(f"{name}: {status.detail}")
    if not services.harness.backends_for("sandbox"):
        return None
    detail = "; ".join(tried) or "no confining backend installed (bwrap on Linux, seatbelt on macOS)"
    msg = f"no confining sandbox available ({detail}); commands run unconfined"
    if st.sandbox_policy == "fail":
        raise SandboxStartupRefused(f"{msg}; refusing to start (sandbox_policy=fail).")
    log.warning("%s (install bubblewrap or set backends.sandbox)", msg)
    await services.audit.record(
        actor="system",
        action="sandbox.fallback",
        decision="warn",
        reason=detail[:500],
        detail={"selected": AUTO, "fallback": FALLBACK},
    )
    fb = services.harness.select_backend("sandbox", FALLBACK)
    assert fb is not None
    return _install(services, fb.load()(st), AUTO, BackendStatus(True, msg[:300]), bool(tried))


async def start(services: Services) -> SandboxBoot | None:
    st = services.settings
    chosen = st.backends.get("sandbox")
    if chosen in (None, AUTO):
        return await _auto(services)
    try:
        bdef = services.harness.select_backend("sandbox", chosen)
    except KeyError as exc:
        bdef, status = None, BackendStatus(False, str(exc))
    if bdef is None and chosen is None:
        return None  # no sandbox backends discovered at all
    selected = chosen or (bdef.manifest.name if bdef else "?")
    backend: Any = None
    if bdef is not None:
        try:
            backend = bdef.load()(st)
            status = await probe(backend)
        except Exception as exc:  # unloadable impl
            backend, status = None, BackendStatus(False, f"failed to load: {exc}"[:300])
    fell_back = False
    if backend is None or not status.ok:
        msg = f"sandbox backend '{selected}' unavailable: {status.detail}"
        if st.sandbox_policy == "fail" or selected == FALLBACK:
            raise SandboxStartupRefused(f"{msg}; refusing to start (sandbox_policy=fail). {status.remedy}")
        log.warning("%s; falling back to '%s' (sandbox_policy=warn)", msg, FALLBACK)
        await services.audit.record(
            actor="system",
            action="sandbox.fallback",
            decision="warn",
            reason=status.detail[:500],
            detail={"selected": selected, "fallback": FALLBACK, "remedy": status.remedy[:300]},
        )
        fb = services.harness.select_backend("sandbox", FALLBACK)
        assert fb is not None
        backend, fell_back = fb.load()(st), True
    return _install(services, backend, selected, status, fell_back)


def _install(
    services: Services, backend: Any, selected: str, status: BackendStatus, fell_back: bool
) -> SandboxBoot:
    async def audit(action: str, detail: dict[str, Any]) -> None:
        await services.audit.record(actor="system", action=action, decision="allow", detail=detail)

    backend.audit = audit
    services._backends["sandbox"] = backend  # what ctx.backend("sandbox") returns
    boot = SandboxBoot(backend, selected, status, fell_back)
    log.info("sandbox: %s", boot.describe())
    return boot
