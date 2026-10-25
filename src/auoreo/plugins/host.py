"""PluginHost: installs, spawns and supervises every enabled `plugins/<name>/` (spec §15).

Each plugin runs as `<venv>/bin/python -m auoreo_plugin_rt <entry> --dir <folder>` with a
scrubbed env (`child_env([])` + `AUOREO_PLUGIN`). What it advertises at `initialize` is
registered dynamically: tools as `plugin__<name>__<tool>`, deliveries in the delivery
registry, skills on the host. A crash never reaches the server: tools answer with an error
and the host restarts the plugin with backoff (1 s → 30 s, at most 5 per hour).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from auoreo.core.secrets import child_env
from auoreo.harness.manifests import PluginManifest
from auoreo.plugins import install as inst
from auoreo.plugins.bridge import (
    PluginDelivery,
    PluginSkill,
    make_tool,
    parse_skills,
    register_delivery,
    tool_prefix,
    unregister_delivery,
)
from auoreo.plugins.connectors import InboundRouter, PairingStore, file_config
from auoreo.plugins.grants import secret_granted
from auoreo.plugins.protocol import RpcError, RpcPeer
from auoreo.tools.context import ToolResult

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)
MAX_RESTARTS_PER_HOUR = 5
BACKOFF_MIN_S, BACKOFF_MAX_S = 1.0, 30.0
WATCH_TICK_S = 1.0


class ConfigError(ValueError):
    pass


def validate_config(manifest: PluginManifest, config: dict[str, Any]) -> None:
    """Raise `ConfigError` naming the JSON pointer of the first (best) schema violation."""
    Draft202012Validator.check_schema(manifest.config_schema)
    err = best_match(Draft202012Validator(manifest.config_schema).iter_errors(config))
    if err is not None:
        pointer = "/" + "/".join(str(p) for p in err.absolute_path)
        raise ConfigError(f"settings plugins.{manifest.name} invalid at {pointer}: {err.message}")


@dataclass
class PluginState:
    name: str
    manifest: PluginManifest
    src: Path
    sha: str = ""
    status: str = "stopped"  # running | stopped | crashed | disabled | invalid_config
    error: str | None = None
    peer: RpcPeer | None = None
    config: dict[str, Any] = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)
    deliveries: list[str] = field(default_factory=list)
    connectors: list[str] = field(default_factory=list)
    skills: list[PluginSkill] = field(default_factory=list)
    registered: list[str] = field(default_factory=list)  # deliveries in the shared registry
    restarts: deque[float] = field(default_factory=deque)
    restart_task: asyncio.Task[None] | None = None
    wanted: bool = True


class PluginHost:
    def __init__(self, services: Services) -> None:
        self.s = services
        self.plugins: dict[str, PluginState] = {}
        self.deliveries: dict[str, PluginDelivery] = {}  # host-local registry (always populated)
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._watch: asyncio.Task[None] | None = None
        self._snap: object | None = None
        self.delivery_registry = False  # True once a delivery registered in backends/delivery

    @property
    def home(self) -> Path:
        return self.s.settings.home

    # ---------------------------------------------------------------- sync
    async def start(self) -> None:
        await self.sync()
        self._watch = asyncio.create_task(self._watch_loop(), name="plugins:watch")

    def _wanted(self) -> dict[str, tuple[PluginManifest, Path]]:
        return {n: (d.manifest, d.path.parent) for n, d in self.s.harness.plugins.items()}

    async def sync(self) -> dict[str, list[str]]:
        """Stop removed/disabled plugins, start new ones, restart those whose folder changed."""
        self._snap = self.s.harness.snapshot
        changes: dict[str, list[str]] = {"started": [], "stopped": [], "restarted": []}
        async with self._lock:
            wanted = self._wanted()
            for name in [n for n in self.plugins if n not in wanted]:
                await self._stop(self.plugins.pop(name), "stopped")
                changes["stopped"].append(name)
            for name, (manifest, src) in wanted.items():
                st = self.plugins.get(name)
                enabled = manifest.enabled and self.s.settings.plugins_enabled
                if st is None:
                    st = self.plugins[name] = PluginState(name, manifest, src)
                    st.status = "stopped" if enabled else "disabled"
                    if enabled:
                        await self._start(st)
                        changes["started"].append(name)
                    continue
                if not enabled:
                    if st.status != "disabled":
                        await self._stop(st, "disabled")
                        changes["stopped"].append(name)
                    st.manifest, st.src = manifest, src
                    continue
                changed = st.manifest != manifest or st.src != src or st.sha != inst.folder_hash(src)
                if st.wanted and (changed or st.status == "disabled"):
                    await self._stop(st, "stopped")
                    st.manifest, st.src, st.wanted = manifest, src, True
                    await self._start(st)
                    changes["restarted"].append(name)
        return changes

    async def _watch_loop(self) -> None:
        """Hot reload without a bus subscriber: a `harness.reload()` swaps the snapshot object,
        which this loop notices within a second; every `plugins_poll_s` a full sync also catches
        plugin folders edited in place (hash change) without a harness reload."""
        last_full = time.monotonic()
        while True:
            await asyncio.sleep(WATCH_TICK_S)
            full = time.monotonic() - last_full >= self.s.settings.plugins_poll_s
            if not full and self.s.harness.snapshot is self._snap:
                continue
            last_full = time.monotonic() if full else last_full
            try:
                await self.sync()
            except Exception:
                log.exception("plugin sync failed")

    # ------------------------------------------------------------ lifecycle
    def config_for(self, name: str) -> dict[str, Any]:
        return {**self.s.settings.plugins.get(name, {}), **file_config(self.home, name)}

    async def _start(self, st: PluginState) -> None:
        st.error = None
        st.config = self.config_for(st.name)
        try:
            validate_config(st.manifest, st.config)
        except Exception as exc:  # fail loud: the plugin stays off until the config is fixed
            st.status, st.error = "invalid_config", str(exc)
            log.error("plugin %s disabled: %s", st.name, exc)
            await self.s.emit("server", "plugin.invalid_config", {"name": st.name, "error": str(exc)})
            return
        try:
            installed = await inst.install(
                st.name,
                st.src,
                self.home,
                python=self.s.settings.plugins_python,
                seed=self.s.settings.plugins_seed,
            )
            st.sha = installed.lock["sha256"]
            await self._spawn(st, installed.python)
        except (inst.InstallError, RpcError, OSError) as exc:
            st.status, st.error = "crashed", str(exc)
            log.error("plugin %s failed to start: %s", st.name, exc)
            await self._cleanup(st)
            await self.s.emit("server", "plugin.crashed", {"name": st.name, "error": str(exc)})
            return
        st.status = "running"
        await self.s.emit(
            "server",
            "plugin.started",
            {"name": st.name, "tools": st.tools, "deliveries": st.deliveries, "connectors": st.connectors},
        )

    async def _spawn(self, st: PluginState, python: Path) -> None:
        env = {**child_env([], self.s.secrets), "AUOREO_PLUGIN": st.name, "PYTHONUNBUFFERED": "1"}
        argv = [str(python), "-m", "auoreo_plugin_rt", st.manifest.entry, "--dir", str(st.src)]

        async def on_request(method: str, params: dict[str, Any]) -> Any:
            return await self._on_request(st, method, params)

        async def on_notify(method: str, params: dict[str, Any]) -> None:
            await self._on_notify(st, method, params)

        async def on_exit(code: int | None) -> None:
            await self._on_exit(st, code)

        peer = RpcPeer(
            st.name, argv, env=env, cwd=st.src, on_request=on_request, on_notify=on_notify, on_exit=on_exit
        )
        st.peer = peer
        await peer.start()
        granted = [c for c in st.manifest.consumes if c in {"sessions", "memory"} or c.startswith("secrets:")]
        info = await peer.request(
            "initialize", {"config": st.config, "consumes_granted": granted}, timeout=30
        )
        self._register(st, info if isinstance(info, dict) else {})
        for conn in st.connectors:
            await peer.request("connector.start", {"name": conn}, timeout=30)

    def _register(self, st: PluginState, info: dict[str, Any]) -> None:
        self.s.harness.unregister_dynamic(tool_prefix(st.name))
        st.tools = []
        for entry in info.get("tools", []):
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            tool = make_tool(
                st.name,
                entry,
                self._caller(st),
                default_timeout_s=self.s.settings.plugins_tool_timeout_s,
            )
            self.s.harness.register_dynamic(tool)
            st.tools.append(tool.name)
        st.deliveries = [str(d) for d in info.get("deliveries", [])]
        st.connectors = [str(c) for c in info.get("connectors", [])]
        st.skills = parse_skills(st.name, list(info.get("skills", [])))
        st.registered = []
        for dname in st.deliveries:
            adapter = PluginDelivery(st.name, dname, self._sender(st))
            self.deliveries[dname] = adapter
            if register_delivery(adapter):
                st.registered.append(dname)
                self.delivery_registry = True

    async def _cleanup(self, st: PluginState) -> None:
        if st.peer is not None:
            await st.peer.close()
            st.peer = None

    async def _stop(self, st: PluginState, status: str) -> None:
        if st.restart_task is not None and not st.restart_task.done():
            st.restart_task.cancel()
        peer = st.peer
        if peer is not None and peer.alive:
            for conn in st.connectors:
                with contextlib.suppress(RpcError):
                    await peer.request("connector.stop", {"name": conn}, timeout=5)
        await self._cleanup(st)
        self.s.harness.unregister_dynamic(tool_prefix(st.name))
        for dname in st.deliveries:
            adapter = self.deliveries.get(dname)
            if adapter is not None and adapter.plugin == st.name:
                del self.deliveries[dname]
        for dname in st.registered:
            unregister_delivery(dname)
        st.tools, st.deliveries, st.connectors, st.skills, st.registered = [], [], [], [], []
        if st.status != status:
            await self.s.emit("server", "plugin.stopped", {"name": st.name, "status": status})
        st.status = status

    async def _on_exit(self, st: PluginState, code: int | None) -> None:
        if st.status not in {"running"}:
            return
        st.status = "crashed"
        tail = st.peer.stderr_tail[-5:] if st.peer else []
        st.error = f"exited with code {code}"
        log.warning("plugin %s crashed (code %s): %s", st.name, code, " | ".join(tail))
        await self.s.emit("server", "plugin.crashed", {"name": st.name, "code": code, "stderr": tail})
        now = time.monotonic()
        while st.restarts and now - st.restarts[0] > 3600:
            st.restarts.popleft()
        if len(st.restarts) >= MAX_RESTARTS_PER_HOUR:
            st.error = f"crashed {len(st.restarts) + 1} times within an hour; not restarting"
            return
        delay = min(BACKOFF_MAX_S, BACKOFF_MIN_S * 2 ** len(st.restarts))
        st.restarts.append(now)
        st.restart_task = asyncio.create_task(
            self._restart_later(st, delay), name=f"plugin:{st.name}:restart"
        )

    async def _restart_later(self, st: PluginState, delay: float) -> None:
        await asyncio.sleep(delay * self.s.settings.plugins_backoff_scale)
        async with self._lock:
            if st.status != "crashed" or self.plugins.get(st.name) is not st:
                return
            st.restart_task = None  # `_stop` cancels a pending restart; not this one
            await self._stop(st, "stopped")
            await self._start(st)
        if st.status == "running":
            await self.s.emit("server", "plugin.restarted", {"name": st.name, "attempt": len(st.restarts)})

    # --------------------------------------------------------- operations
    def _get(self, name: str) -> PluginState:
        st = self.plugins.get(name)
        if st is None:
            raise KeyError(f"unknown plugin '{name}' (known: {sorted(self.plugins)})")
        return st

    async def start_plugin(self, name: str) -> PluginState:
        async with self._lock:
            st = self._get(name)
            if st.status != "running":
                await self._stop(st, "stopped")
                st.wanted = True
                await self._start(st)
            return st

    async def stop_plugin(self, name: str) -> PluginState:
        async with self._lock:
            st = self._get(name)
            await self._stop(st, "stopped")
            st.wanted = False
            return st

    async def reload_plugin(self, name: str) -> PluginState:
        """Re-read definitions from disk, then restart this plugin."""
        self.s.harness.reload()
        async with self._lock:
            wanted = self._wanted()
            if name not in wanted:
                st = self.plugins.pop(name, None)
                if st is not None:
                    await self._stop(st, "stopped")
                raise KeyError(f"plugin '{name}' is no longer defined")
            st = self.plugins.setdefault(name, PluginState(name, *wanted[name]))
            await self._stop(st, "stopped")
            st.manifest, st.src = wanted[name]
            st.wanted = True
            if st.manifest.enabled and self.s.settings.plugins_enabled:
                await self._start(st)
            else:
                st.status = "disabled"
            return st

    async def pair(self, name: str, code: str) -> str | None:
        st = self._get(name)
        chat_id = PairingStore(self.home, name).pair(code)
        if chat_id is None:
            return None
        st.config = self.config_for(name)
        await self.s.audit.record(
            actor=f"plugin:{name}", action="plugin.pair", decision="allow", detail={"chat_id": chat_id}
        )
        await self.s.emit("server", "plugin.paired", {"name": name, "chat_id": chat_id})
        if st.peer is not None and st.peer.alive:
            with contextlib.suppress(RpcError):
                await st.peer.request("config.update", {"config": st.config}, timeout=10)
        return chat_id

    # ------------------------------------------------------- plugin → host
    def _caller(self, st: PluginState) -> Any:
        async def call(remote: str, args: dict[str, Any], session_id: str, timeout: float) -> ToolResult:
            peer = st.peer
            if peer is None or not peer.alive:
                return ToolResult(f"plugin '{st.name}' is not running ({st.status})", is_error=True)
            try:
                res = await peer.request(
                    "tool.call", {"name": remote, "args": args, "session_id": session_id}, timeout=timeout
                )
            except RpcError as exc:
                return ToolResult(f"plugin '{st.name}' tool '{remote}' failed: {exc}", is_error=True)
            res = res if isinstance(res, dict) else {}
            return ToolResult(str(res.get("content", "")), is_error=bool(res.get("is_error")))

        return call

    def _sender(self, st: PluginState) -> Any:
        async def send(name: str, target: str, text: str, meta: dict[str, Any]) -> dict[str, Any]:
            peer = st.peer
            if peer is None or not peer.alive:
                return {"ok": False, "detail": f"plugin '{st.name}' is not running", "external_id": None}
            params = {"name": name, "target": target, "text": text, "meta": meta}
            res = await peer.request("delivery.send", params, timeout=self.s.settings.plugins_tool_timeout_s)
            return res if isinstance(res, dict) else {"ok": False, "detail": "bad reply"}

        return send

    async def _on_request(self, st: PluginState, method: str, params: dict[str, Any]) -> Any:
        if method == "secret.get":
            name = str(params.get("name", ""))
            allowed = secret_granted(name, st.manifest.consumes)
            await self.s.audit.record(
                actor=f"plugin:{st.name}",
                action="plugin.secret",
                decision="allow" if allowed else "deny",
                detail={"secret": name},
            )
            if not allowed:
                raise RpcError(f"secret '{name}' is not declared in consumes", code=-32001)
            return {"value": self.s.secrets.get(name)}
        if method == "pairing.request":
            store = PairingStore(self.home, st.name)
            chat_id = str(params.get("chat_id", ""))
            if chat_id in store.allowlist(list(st.config.get("allowlist", []))):
                return {"allowed": True}
            code = store.request(chat_id, str(params.get("user", "")), str(params.get("name", "")))
            await self.s.emit(
                "server",
                "plugin.pairing_requested",
                {"name": st.name, "chat_id": chat_id, "user": params.get("user")},
            )
            return {"allowed": False, "code": code}
        raise RpcError(f"unknown method {method}", code=-32601)

    async def _on_notify(self, st: PluginState, method: str, params: dict[str, Any]) -> None:
        if method == "log":
            level = getattr(logging, str(params.get("level", "info")).upper(), logging.INFO)
            log.log(
                level if isinstance(level, int) else logging.INFO,
                "plugin %s: %s",
                st.name,
                params.get("message"),
            )
        elif method == "connector.message":
            task = asyncio.create_task(self._inbound(st, params), name=f"plugin:{st.name}:inbound")
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _inbound(self, st: PluginState, params: dict[str, Any]) -> dict[str, Any] | None:
        if "sessions" not in st.manifest.consumes:
            log.warning("plugin %s sent a message but does not consume 'sessions'", st.name)
            return None
        chat_id = str(params.get("chat_id", ""))
        gated = "allowlist" in st.manifest.config_schema.get("properties", {})  # host re-checks pairing
        allow = list(st.config.get("allowlist") or [])
        if gated and chat_id not in PairingStore(self.home, st.name).allowlist(allow):
            log.warning("plugin %s: dropping message from non-allowlisted chat %s", st.name, chat_id)
            return None
        delivery = str(params.get("name")) if params.get("name") in st.deliveries else None
        delivery = delivery or (st.deliveries[0] if st.deliveries else None)
        sender = self._sender(st)

        async def send(target: str, text: str, meta: dict[str, Any]) -> dict[str, Any]:
            if delivery is None:
                return {"ok": False, "detail": "plugin provides no delivery"}
            try:
                res: dict[str, Any] = await sender(delivery, target, text, meta)
                return res
            except RpcError as exc:
                return {"ok": False, "detail": str(exc)}

        try:
            out = await InboundRouter(self.s, st.name).handle(params, send, st.config.get("agent"))
        except Exception:
            log.exception("plugin %s: inbound message failed", st.name)
            return None
        await self.s.emit("server", "plugin.message_handled", {"name": st.name, "chat_id": chat_id, **out})
        return out

    # --------------------------------------------------------------- views
    def rows(self) -> list[dict[str, Any]]:
        out = []
        for name, st in sorted(self.plugins.items()):
            out.append(
                {
                    "name": name,
                    "version": st.manifest.version,
                    "description": st.manifest.description,
                    "status": st.status,
                    "provides": list(st.manifest.provides),
                    "tools": list(st.tools),
                    "deliveries": list(st.deliveries),
                    "connectors": list(st.connectors),
                    "error": st.error,
                    "verify": inst.verify(self.home, name, st.src),
                }
            )
        return out

    def skills(self, name: str) -> list[PluginSkill]:
        return list(self._get(name).skills)

    async def close(self) -> None:
        if self._watch is not None:
            self._watch.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch
        for task in list(self._tasks):
            task.cancel()
        async with self._lock:
            for st in self.plugins.values():
                await self._stop(st, "stopped")


async def start(services: Services) -> PluginHost:
    """Extension starter (`core/extensions.STARTERS`) → `services.ext["host"]`."""
    host = PluginHost(services)
    await host.start()
    return host
