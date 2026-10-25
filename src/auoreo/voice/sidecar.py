"""Control-plane client of the `auoreo voice-host` media sidecar (F6).

`backends/{stt,tts,vad}/sidecar` proxy to one shared `VoiceHost` per (interpreter, inner
backends, home) and event loop. The host is started lazily on the first call, answers a
`check` health probe, and is restarted with backoff when it dies unexpectedly - at most
`MAX_RESTARTS` times per `WINDOW_S`; after that every call (and `check()`) fails honestly until
the crashes age out. A request that times out fails alone; the process keeps running. The child
gets `core.secrets.child_env` (allowlist) plus only the inner backends' declared `requires_env`.
Shutdown: each sidecar backend's `close()` (run by `Services.close_backends`) closes the host.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.plugins.protocol import RpcError, RpcPeer
from auoreo.tools.context import ToolError
from auoreo.voice.host import DEBUG_ENV, KINDS, MAX_PAYLOAD_BYTES, SETTINGS_ENV, TOOL_ERROR

log = logging.getLogger(__name__)

MAX_RESTARTS = 3
WINDOW_S = 60.0
BACKOFF_S = (0.0, 0.25, 0.5, 1.0)
SETTINGS_FIELDS = ("home", "extra_roots", "backends", "keyring", "offline")
_HOSTS: dict[tuple[Any, ...], VoiceHost] = {}


class VoiceHostError(ToolError):
    """The sidecar is unavailable (crash loop, failed start, timeout)."""


def _settings_snapshot(settings: Any) -> str:
    """Non-secret settings the child needs (env is scrubbed, so AUOREO_* overrides travel here)."""
    dump = settings.model_dump(mode="json")
    keep = {k: v for k, v in dump.items() if k.startswith("voice_") or k in SETTINGS_FIELDS}
    return json.dumps(keep)


def required_env(settings: Any) -> list[str]:
    """`requires_env` declared by the inner backends the sidecar will load."""
    from auoreo.core.paths import bundled_root
    from auoreo.harness.discovery import default_roots
    from auoreo.harness.registry import Harness

    names: list[str] = []
    try:
        h = Harness(default_roots(settings.home, Path.cwd(), bundled_root(), settings.extra_roots))
        for kind, name in settings.voice_host_backends.model_dump().items():
            bdef = h.select_backend(kind, name)
            if bdef is not None:
                names.extend(bdef.manifest.requires_env)
    except Exception as exc:  # the child reports the broken definition through `check`
        log.warning("voice-host: cannot read inner backend manifests: %s", exc)
    return sorted(set(names))


class VoiceHost:
    def __init__(
        self,
        settings: Any,
        *,
        python: str | None = None,
        timeout: float | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self.settings = settings
        self.python = python or getattr(settings, "voice_host_python", None) or sys.executable
        self.timeout: float = timeout or float(getattr(settings, "voice_host_timeout_s", 60.0))
        self.extra_env = dict(extra_env or {})
        self.peer: RpcPeer | None = None
        self.crashes: list[float] = []
        self.starts = 0
        self.last_error = ""
        self.loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ lifecycle
    @property
    def pid(self) -> int | None:
        p = self.peer
        return p.proc.pid if p is not None and p.alive and p.proc is not None else None

    def recent_crashes(self) -> int:
        now = time.monotonic()
        self.crashes = [t for t in self.crashes if now - t < WINDOW_S]
        return len(self.crashes)

    def env(self) -> dict[str, str]:
        from auoreo.core.secrets import SecretStore, child_env

        st = self.settings
        env = child_env(required_env(st), SecretStore(st.home, st.keyring))
        env.update({"AUOREO_HOME": str(st.home), SETTINGS_ENV: _settings_snapshot(st)})
        if bundled := os.environ.get("AUOREO_BUNDLED_ROOT"):
            env["AUOREO_BUNDLED_ROOT"] = bundled
        env.update(self.extra_env)
        return env

    async def _on_exit(self, code: int | None) -> None:
        self.crashes.append(time.monotonic())
        tail = "; ".join(self.peer.stderr_tail[-3:]) if self.peer is not None else ""
        self.last_error = f"voice-host exited (code {code}){': ' + tail if tail else ''}"
        log.warning("%s", self.last_error)

    async def _ensure(self) -> RpcPeer:
        async with self._lock:
            if self.peer is not None and self.peer.alive:
                return self.peer
            n = self.recent_crashes()
            if n > MAX_RESTARTS:
                raise VoiceHostError(
                    f"voice-host crashed {n} times in {WINDOW_S:g}s; giving up until it settles "
                    f"(last: {self.last_error})"
                )
            if self.peer is not None:
                await self.peer.close(timeout=1.0)
            await asyncio.sleep(BACKOFF_S[min(n, len(BACKOFF_S) - 1)])
            peer = RpcPeer(
                "voice-host",
                [self.python, "-m", "auoreo.voice.host"],
                env=self.env(),
                cwd=Path.cwd(),
                on_request=_no_requests,
                on_notify=_ignore,
                on_exit=self._on_exit,
            )
            try:
                await peer.start()
            except OSError as exc:
                self.crashes.append(time.monotonic())
                self.last_error = f"cannot start {self.python}: {exc}"
                raise VoiceHostError(self.last_error) from exc
            self.peer, self.loop = peer, asyncio.get_running_loop()
            self.starts += 1
            return peer

    async def close(self) -> None:
        peer, self.peer = self.peer, None
        if peer is not None:
            await peer.close(timeout=2.0)

    # ------------------------------------------------------------------ calls
    async def call(self, method: str, params: dict[str, Any], timeout: float | None = None) -> Any:
        peer = await self._ensure()
        try:
            return await peer.request(method, params, timeout=timeout or self.timeout)
        except RpcError as exc:
            if exc.code == TOOL_ERROR:
                raise ToolError(str(exc)) from exc
            if not peer.alive:
                raise VoiceHostError(f"voice-host died during {method}: {exc}") from exc
            raise VoiceHostError(f"voice-host: {exc}") from exc

    async def check(self) -> BackendStatus:
        try:
            info = await self.call("check", {}, timeout=min(self.timeout, 30.0))
        except ToolError as exc:
            return BackendStatus(False, str(exc), "see `auoreo voice-host --check` and the sidecar stderr")
        rows = info.get("backends", {})
        bad = [f"{k}: {r.get('detail')}" for k, r in rows.items() if not r.get("ok")]
        names = ", ".join(f"{k}={r.get('name')}" for k, r in rows.items())
        detail = f"voice-host pid {info.get('pid')} ({names})"
        if bad:
            return BackendStatus(
                False, f"{detail}; " + "; ".join(bad), "fix voice_host_backends / the sidecar venv"
            )
        return BackendStatus(True, detail)


def encode(data: bytes) -> str:
    if len(data) > MAX_PAYLOAD_BYTES:
        raise ToolError(
            f"audio is {len(data)} bytes; the voice-host payload cap is {MAX_PAYLOAD_BYTES} (25 MB)"
        )
    return base64.b64encode(data).decode()


async def _no_requests(method: str, _params: dict[str, Any]) -> Any:
    raise RpcError(f"voice-host may not call {method}", -32601)


async def _ignore(_method: str, _params: dict[str, Any]) -> None:
    return None


def get_host(settings: Any) -> VoiceHost:
    """The shared host for this config and event loop (a host from a closed loop is dropped)."""
    key = (
        getattr(settings, "voice_host_python", None),
        json.dumps(settings.voice_host_backends.model_dump(), sort_keys=True),
        str(settings.home),
    )
    host = _HOSTS.get(key)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if host is not None and host.loop is not None and host.loop is not loop:
        if host.peer is not None and host.peer.proc is not None and host.peer.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                host.peer.proc.kill()
        host = None
    if host is None:
        host = _HOSTS[key] = VoiceHost(settings)
    return host


async def close_all() -> None:
    for key, host in list(_HOSTS.items()):
        _HOSTS.pop(key, None)
        await host.close()


__all__ = ["KINDS", "DEBUG_ENV", "VoiceHost", "VoiceHostError", "close_all", "encode", "get_host"]
