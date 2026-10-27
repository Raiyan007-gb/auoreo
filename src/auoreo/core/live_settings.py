"""Live settings: apply state.db settings / secrets.db changes to the running server.

`apply(svc, new)` diffs the top-level fields, copies the live ones INTO the existing
`svc.settings` object (every holder of the reference — runner, executor, gateway, backends,
middleware — sees them from its next read) and runs the per-area refresh paths in
`core.live_handlers` (gateway Router + credentials, harness reload, plugins, scheduler, hooks,
voice, observability, cached backends, custom-variable redaction). Fields only read at startup
(`env_catalog.RESTART_REQUIRED`) and security knobs (`[env-only]`, frozen from the real
environment) are NOT applied: they are reported as `restart_required`.

A step already in flight keeps its frozen request; the agent sees new values from its next step
(the runner builds the spec + context per turn and reads `svc.settings` per step).

`LiveSettings` (the `live_settings` extension) also polls the settings version counter
(`app_settings_meta.version`, bumped by every write from any process) and the secrets.db mtime
(every `POLL_S`) so `auoreo config set` and `auoreo secrets set` apply live too;
API writes call `reload()` / `secrets_changed()` directly. Events carry names only:
`settings.changed {keys, live, restart_required, source}`, `secret.changed {name, action}`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auoreo.core import env_store, live_handlers, settings_store
from auoreo.core.env_catalog import ENV_ONLY_RESTART, RESTART_REQUIRED, by_field

log = logging.getLogger(__name__)
POLL_S = 2.0


@dataclass
class AppliedChange:
    changed: list[str] = field(default_factory=list)
    live: list[str] = field(default_factory=list)
    restart_required: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def _dump(settings: Any) -> dict[str, Any]:
    return settings.model_dump(mode="json")  # type: ignore[no-any-return]


def restart_reason(key: str) -> str | None:
    if key in RESTART_REQUIRED:
        return RESTART_REQUIRED[key]
    entry = by_field().get(key)
    return ENV_ONLY_RESTART if entry is not None and entry.tier == "env-only" else None


def diff(old: Any, new: Any) -> list[str]:
    a, b = _dump(old), _dump(new)
    return sorted(k for k in {*a, *b} if k != "home" and a.get(k) != b.get(k))


async def apply(svc: Any, new: Any) -> AppliedChange:
    """Copy the live part of `new` into `svc.settings` in place, then refresh caches."""
    from auoreo.core.settings import get_settings

    changed = diff(svc.settings, new)
    live = [k for k in changed if restart_reason(k) is None]
    out = AppliedChange(changed, live, [k for k in changed if k not in live])
    for key in live:
        setattr(svc.settings, key, getattr(new, key))
    get_settings.cache_clear()  # module-level readers (`get_settings()`) re-read the store
    if live:
        out.failed = await live_handlers.run(svc, live)
    return out


class LiveSettings:
    def __init__(self, svc: Any) -> None:
        self.svc = svc
        self.home = Path(svc.settings.home)
        self.overrides = self._overrides()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._mtimes = self._stat()
        self.pending: list[str] = []
        """Changed keys that wait for a restart (from the last reload)."""

    # ------------------------------------------------------------ build
    def _overrides(self) -> dict[str, Any]:
        """Init values the running Settings was built with (CLI flags, tests): they keep
        winning over settings.json on every rebuild, exactly as at startup."""
        try:
            baseline = self._fresh({})
        except Exception:  # an already-broken store: no overrides can be inferred
            return {}
        a, b = _dump(self.svc.settings), _dump(baseline)
        return {k: getattr(self.svc.settings, k) for k in a if k != "home" and a[k] != b.get(k)}

    def _fresh(self, overrides: dict[str, Any]) -> Any:
        from auoreo.core.settings import Settings

        return Settings(home=self.home, **overrides)

    def build(self) -> Any:
        """Settings as a restart would build them now (env > overrides > state.db)."""
        return self._fresh(self.overrides)

    # ------------------------------------------------------------ apply
    async def reload(self, source: str = "watch") -> AppliedChange:
        async with self._lock:
            self._mtimes = self._stat()
            env_store.read(self.home)  # an unreadable store raises (Settings would ignore it)
            new = self.build()  # raises ValidationError for an invalid value (watcher logs it)
            res = await apply(self.svc, new)
            self.pending = res.restart_required
        if res.changed:
            await self.svc.emit(
                "server",
                "settings.changed",
                {
                    "keys": res.changed,
                    "live": res.live,
                    "restart_required": res.restart_required,
                    "source": source,
                },
            )
        return res

    async def secrets_changed(self, name: str | None, action: str) -> None:
        """A secret was written: new provider keys reach the gateway, custom secret values the
        redactor; `secret.changed` names it (never the value)."""
        if (reload := getattr(self.svc.gateway, "reload_credentials", None)) is not None:
            try:
                reload()
            except Exception:
                log.exception("gateway credential reload failed")
        await self.note_secret_write(name, action)

    async def note_secret_write(self, name: str | None, action: str) -> None:
        """Bookkeeping after a secrets.db write whose caller already reloaded the gateway."""
        from auoreo.core.env_custom import refresh_redaction

        self._mtimes = self._stat()
        refresh_redaction(self.svc)
        await self.svc.emit("server", "secret.changed", {"name": name, "action": action})

    # ------------------------------------------------------------ watcher
    def _stat(self) -> tuple[float | None, float | None]:
        def mtime(p: Path) -> float | None:
            try:
                return p.stat().st_mtime_ns / 1e9
            except OSError:
                return None

        try:
            version: float | None = float(settings_store.version(self.home))
        except Exception:  # mid-migration / locked: try again on the next poll
            version = None
        return version, mtime(self.home / "secrets.db")

    async def poll_once(self) -> None:
        now = self._stat()
        before, self._mtimes = self._mtimes, now
        if now[0] != before[0]:
            try:
                await self.reload("watch")
            except Exception as exc:  # an invalid value written behind our back: keep running
                log.warning("stored settings changed but cannot be applied: %s", exc)
        if now[1] != before[1]:
            await self.secrets_changed(None, "external")

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(POLL_S)
            try:
                await self.poll_once()
            except Exception:
                log.exception("live settings watcher failed")

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._watch(), name="live_settings:watch")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None


def live_of(svc: Any) -> LiveSettings:
    """The running instance, or a passive one (extensions off: CLI, some tests)."""
    found = svc.ext.get("live_settings")
    if isinstance(found, LiveSettings):
        return found
    inst = LiveSettings(svc)
    svc.ext["live_settings"] = inst
    return inst


async def start(services: Any) -> LiveSettings:
    """Extension starter → `services.ext["live_settings"]` (watcher on)."""
    from auoreo.core.env_custom import refresh_redaction

    await record_migration(services)
    live = LiveSettings(services)
    refresh_redaction(services)
    live.start()
    return live


async def record_migration(services: Any) -> None:
    """Import a pre-v8 settings.json (if any is left) and audit the one-time migration once."""
    import json

    home = Path(services.settings.home)
    try:
        await asyncio.to_thread(settings_store.migrate_legacy, home)
        note = await asyncio.to_thread(settings_store.get_meta, home, "pending_audit")
    except Exception:
        log.exception("settings.json migration failed; the file was left in place")
        return
    if not note:
        return
    await services.audit.record(
        actor="system", action="settings.migrate", decision="allow", detail=json.loads(note)
    )
    await asyncio.to_thread(settings_store.set_meta, home, "pending_audit", None)


__all__ = ["AppliedChange", "LiveSettings", "apply", "diff", "live_of", "restart_reason", "start"]
