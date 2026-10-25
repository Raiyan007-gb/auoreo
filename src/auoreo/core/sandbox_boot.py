"""Sandbox startup probe (spec §19.8): never a silent downgrade.

Probes the selected `sandbox` backend once at startup. On failure, `sandbox_policy`
decides: `warn` logs, audits `sandbox.fallback` and installs the `none` backend; `fail`
refuses to start. The result is `services.ext["sandbox_boot"]`, and the chosen instance
is what `ctx.backend("sandbox")` returns.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from auoreo.backends import BackendStatus

if TYPE_CHECKING:
    from auoreo.backends.sandbox import SandboxBackend
    from auoreo.core.services import Services

log = logging.getLogger(__name__)
PROBE_TIMEOUT_S = 10.0
FALLBACK = "none"


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


async def start(services: Services) -> SandboxBoot | None:
    st = services.settings
    chosen = st.backends.get("sandbox")
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

    async def audit(action: str, detail: dict[str, Any]) -> None:
        await services.audit.record(actor="system", action=action, decision="allow", detail=detail)

    backend.audit = audit
    services._backends["sandbox"] = backend  # what ctx.backend("sandbox") returns
    boot = SandboxBoot(backend, selected, status, fell_back)
    log.info("sandbox: %s", boot.describe())
    return boot
