"""Xiaomi miIO tools for AUOREO (python-miio, imported lazily inside the plugin venv).

`miio_info(device)` → model / firmware / hardware / MAC (never the raw info payload, which
echoes the token); `miio_call(device, method, params)` → `Device(ip, token).send(method,
params)`. The host asks for approval before every call unless a permission rule allows it.
Tokens are read from the secret store (`token_secret`, e.g. `MIIO_TOKEN_PURIFIER`) on first use
and never appear in results or logs. python-miio talks UDP and blocks, so calls run in a
worker thread with `timeout_s`.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any

METHOD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
TOKEN_RE = re.compile(r"^[0-9a-fA-F]{32}$")
INSTALL_HINT = "python-miio is not installed in the plugin venv (pip install python-miio)"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "miio_info",
        "description": "Model, firmware and hardware of a configured Xiaomi miIO device.",
        "parameters": {
            "type": "object",
            "properties": {"device": {"type": "string", "description": "Configured device name"}},
            "required": ["device"],
            "additionalProperties": False,
        },
    },
    {
        "name": "miio_call",
        "description": "Send a raw miIO method + params to a configured device (e.g. get_prop, set_power).",
        "parameters": {
            "type": "object",
            "properties": {
                "device": {"type": "string"},
                "method": {"type": "string", "pattern": METHOD_RE.pattern},
                "params": {"type": ["array", "object"], "default": []},
            },
            "required": ["device", "method"],
            "additionalProperties": False,
        },
    },
]


class MiioError(RuntimeError):
    pass


def _miio() -> Any:
    import miio

    return miio


class Plugin:
    def __init__(self, host: Any, importer: Callable[[], Any] | None = None) -> None:
        self.host = host
        self.importer = importer or _miio
        self.config: dict[str, Any] = {}
        self._tokens: dict[str, str] = {}

    # ------------------------------------------------------------- protocol
    def initialize(self, config: dict[str, Any], consumes_granted: list[str]) -> dict[str, Any]:
        self.config_update(config)
        status = self.check()
        if not status["ok"]:
            self.host.log("warning", f"miio: {status['detail']}")
        return {"tools": TOOLS, "deliveries": [], "connectors": [], "skills": []}

    def config_update(self, config: dict[str, Any]) -> dict[str, Any]:
        self.config = dict(config)
        self._tokens.clear()
        return {}

    @property
    def devices(self) -> dict[str, dict[str, Any]]:
        return {str(d["name"]): d for d in self.config.get("devices", [])}

    def check(self) -> dict[str, Any]:
        try:
            mod = self.importer()
        except ImportError:
            return {"ok": False, "detail": INSTALL_HINT}
        version = getattr(mod, "__version__", "?")
        return {"ok": True, "detail": f"python-miio {version}; {len(self.devices)} device(s)"}

    # --------------------------------------------------------------- tools
    async def call_tool(self, name: str, args: dict[str, Any], session_id: str) -> dict[str, Any]:
        handlers = {"miio_info": self.info, "miio_call": self.call}
        fn = handlers.get(name)
        if fn is None:
            return {"content": f"unknown tool {name}", "is_error": True}
        try:
            return {"content": json.dumps(await fn(args), default=str), "is_error": False}
        except MiioError as exc:
            return {"content": str(exc), "is_error": True}
        except Exception as exc:  # device errors / timeouts: no token, no raw payload
            msg = str(exc)
            for token in self._tokens.values():
                msg = msg.replace(token, "[REDACTED]")
            return {"content": f"miio {name} failed: {type(exc).__name__}: {msg}"[:500], "is_error": True}

    async def _device(self, name: str) -> tuple[Any, dict[str, Any]]:
        dev = self.devices.get(name)
        if dev is None:
            raise MiioError(f"unknown device '{name}' (configured: {sorted(self.devices)})")
        try:
            mod = self.importer()
        except ImportError as exc:
            raise MiioError(INSTALL_HINT) from exc
        secret_name = str(dev["token_secret"])
        token = self._tokens.get(secret_name)
        if token is None:
            try:
                token = await self.host.secret(secret_name)
            except Exception as exc:
                raise MiioError(
                    f"secret {secret_name} refused by the host ({exc}); declare "
                    f"'secrets:{secret_name}' in plugins/miio/plugin.yaml consumes"
                ) from None
            if not token or not TOKEN_RE.match(token):
                raise MiioError(f"secret {secret_name} is missing or not a 32-hex miIO token")
            self._tokens[secret_name] = token
        kwargs: dict[str, Any] = {"timeout": int(self.config.get("timeout_s", 5))}
        if dev.get("model"):
            kwargs["model"] = str(dev["model"])  # skips python-miio's model auto-detection
        return mod.Device(str(dev["ip"]), token, **kwargs), dev

    async def info(self, args: dict[str, Any]) -> dict[str, Any]:
        device, dev = await self._device(str(args.get("device", "")))
        info = await asyncio.to_thread(device.info)
        return {
            "device": dev["name"],
            "ip": dev["ip"],
            "model": getattr(info, "model", None) or dev.get("model"),
            "firmware_version": getattr(info, "firmware_version", None),
            "hardware_version": getattr(info, "hardware_version", None),
            "mac_address": getattr(info, "mac_address", None),
        }

    async def call(self, args: dict[str, Any]) -> dict[str, Any]:
        method = str(args.get("method", ""))
        if not METHOD_RE.match(method):
            raise MiioError(f"invalid miIO method name '{method}'")
        params = args.get("params", [])
        if not isinstance(params, list | dict):
            raise MiioError("params must be a JSON array or object")
        device, dev = await self._device(str(args.get("device", "")))
        result = await asyncio.to_thread(device.send, method, params)
        return {"device": dev["name"], "method": method, "result": result}
