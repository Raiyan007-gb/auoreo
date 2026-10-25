"""Session-scoped sensor probes for `sensor_read` (spec L629, L846, L1098).

A probe is constructed and `start()`ed the first time a session reads `(probe, source)` and
stays open for that session's later reads; it is stopped when the session is archived or
deleted (bus events, via `SessionScoped`), after `IDLE_S` without use, and when the server
closes. Sources may embed credentials (`rtsp://user:pass@cam/…`): every log line and audit
record uses `redact_source`.
"""

from __future__ import annotations

import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from auoreo.backends.sensor import Sample, SensorProbe
from auoreo.core.redaction import redact
from auoreo.perception import rtc
from auoreo.tools.context import ToolError
from auoreo.tools.shell_sessions import SessionScoped, attached

log = logging.getLogger(__name__)
IDLE_S = 10 * 60.0
MAX_PROBES = 4


def redact_source(source: str) -> str:
    """Drop any `user[:password]@` from a URL source (and query secrets via core redaction)."""
    try:
        parts = urlsplit(source)
    except ValueError:
        return redact(source)
    if parts.scheme and "@" in parts.netloc:
        host = parts.netloc.rsplit("@", 1)[1]
        source = urlunsplit((parts.scheme, f"***@{host}", parts.path, parts.query, parts.fragment))
    return redact(source)


@dataclass
class OpenProbe:
    name: str
    source: str
    probe: SensorProbe
    started_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)
    samples: int = 0


class SensorRegistry(SessionScoped):
    def __init__(self, services: Any, idle_s: float = IDLE_S) -> None:
        super().__init__(services)
        self.idle_s = idle_s
        self._open: dict[str, dict[tuple[str, str], OpenProbe]] = {}

    def live(self, session_id: str) -> dict[tuple[str, str], OpenProbe]:
        return self._open.get(session_id, {})

    def _construct(self, name: str) -> SensorProbe:
        svc = self.services
        harness = getattr(svc, "harness", None)
        if harness is None:
            raise ToolError("no harness attached; sensor probes unavailable")
        cands = harness.backends_for("sensor")
        if name not in cands:
            raise ToolError(f"unknown sensor probe '{name}' (known: {sorted(cands)})")
        probe = cands[name].load()(svc.settings)
        if not isinstance(probe, SensorProbe):
            raise ToolError(f"backends/sensor/{name} does not implement SensorProbe")
        return probe

    def default_probe(self) -> str:
        bdef = self.services.harness.select_backend("sensor", self.services.settings.backends.get("sensor"))
        if bdef is None:
            raise ToolError("no sensor probe selected; pass `probe` or set settings.backends.sensor")
        return str(bdef.manifest.name)

    async def acquire(self, session_id: str, name: str, source: str) -> OpenProbe:
        self.ensure_tasks()
        mine = self._open.setdefault(session_id, {})
        key = (name, source)
        if key in mine:
            mine[key].last_used = time.monotonic()
            return mine[key]
        if len(mine) >= MAX_PROBES:
            raise ToolError(f"too many open probes in this session ({len(mine)}/{MAX_PROBES})")
        probe = self._construct(name)
        status = await probe.check()
        if not status.ok:
            hint = f" ({status.remedy})" if status.remedy else ""
            raise ToolError(f"sensor probe '{name}' unavailable: {status.detail}{hint}")
        try:
            await probe.start(source)
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(
                f"cannot start probe '{name}' on {redact_source(source)}: {type(exc).__name__}: "
                f"{redact_source(str(exc))}"[:400]
            ) from None
        op = mine[key] = OpenProbe(name, source, probe)
        log.info("sensor %s started on %s (session %s)", name, redact_source(source), session_id)
        await self._audit("sensor.start", session_id, op)
        return op

    async def read(self, op: OpenProbe) -> Sample:
        op.last_used = time.monotonic()
        try:
            sample = await op.probe.sample()
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(
                f"probe '{op.name}' failed on {redact_source(op.source)}: {type(exc).__name__}"
            ) from None
        op.samples += 1
        return sample

    async def _stop(self, session_id: str, op: OpenProbe, reason: str) -> None:
        with contextlib.suppress(Exception):
            await op.probe.stop()
        log.info("sensor %s stopped on %s (%s)", op.name, redact_source(op.source), reason)
        await self._audit("sensor.stop", session_id, op, reason)

    async def _audit(self, action: str, session_id: str, op: OpenProbe, reason: str = "") -> None:
        audit = getattr(self.services, "audit", None)
        if audit is None:
            return
        with contextlib.suppress(Exception):
            await audit.record(
                actor="tool",
                action=action,
                session_id=session_id,
                reason=reason,
                detail={"probe": op.name, "source": redact_source(op.source), "samples": op.samples},
            )

    async def release(self, session_id: str, name: str, source: str) -> bool:
        op = self._open.get(session_id, {}).pop((name, source), None)
        if op is not None:
            await self._stop(session_id, op, "released")
        return op is not None

    async def close_session(self, session_id: str) -> None:
        for op in list(self._open.pop(session_id, {}).values()):
            await self._stop(session_id, op, "session closed")
        rtc.forget(session_id)

    async def reap_idle(self) -> None:
        now = time.monotonic()
        for sid, mine in list(self._open.items()):
            for key, op in list(mine.items()):
                if now - op.last_used > self.idle_s:
                    mine.pop(key, None)
                    await self._stop(sid, op, "idle")

    async def close_all(self) -> None:
        for sid in list(self._open):
            await self.close_session(sid)


def registry_for(services: Any) -> SensorRegistry:
    return attached(services, "sensor_probes", SensorRegistry)
