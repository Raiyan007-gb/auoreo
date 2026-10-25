"""What a running plugin provides, adapted to the host: dynamic tools (`plugin__<name>__<tool>`),
delivery providers (registered in `auoreo.backends.delivery` when that module exists) and
skills (kept here; served by `GET /api/plugins/{name}/skills`)."""

from __future__ import annotations

import importlib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from auoreo.harness.loader import ToolDef
from auoreo.harness.manifests import ToolManifest
from auoreo.tools.context import ToolContext, ToolResult

log = logging.getLogger(__name__)
PREFIX = "plugin__"
DELIVERY_MODULE = "auoreo.backends.delivery"

ToolCaller = Callable[[str, dict[str, Any], str, float], Awaitable[ToolResult]]
Sender = Callable[[str, str, str, dict[str, Any]], Awaitable[dict[str, Any]]]


def safe(part: str) -> str:
    out = re.sub(r"[^a-z0-9_]", "_", part.lower())
    return out if out[:1].isalpha() else f"t_{out}"


def tool_prefix(plugin: str) -> str:
    return f"{PREFIX}{safe(plugin)}__"


def tool_name(plugin: str, tool: str) -> str:
    return f"{tool_prefix(plugin)}{safe(tool)}"[:64]


def make_tool(
    plugin: str,
    entry: dict[str, Any],
    call: ToolCaller,
    *,
    default_timeout_s: float,
    toolset: str | None = None,
    name: str | None = None,
    exposure: str = "deferred",
) -> ToolDef:
    """One plugin-advertised tool → a dynamic `ToolDef` (same pattern as the MCP bridge)."""
    remote = str(entry["name"])
    full = name or tool_name(plugin, remote)
    params = dict(entry.get("parameters") or {"type": "object", "properties": {}})
    params.setdefault("type", "object")
    timeout_ms = int(entry.get("timeout_ms") or default_timeout_s * 1000)
    tm = ToolManifest(
        name=full,
        description=str(entry.get("description") or remote)[:1024],
        toolset=toolset or f"plugin:{plugin}",
        parameters=params,
        permission=full,
        default_action="ask",
        exposure=exposure,
        timeout_ms=min(max(timeout_ms + 5_000, 100), 3_600_000),  # the host enforces the real limit
        search_hint=f"{plugin} {remote} plugin",
    )

    async def handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return await call(remote, args, ctx.session_id, timeout_ms / 1000)

    return ToolDef(tm, tm.toolset, None, lambda: handler)


# --------------------------------------------------------------- delivery
@dataclass
class Receipt:
    """Stand-in for `DeliveryReceipt` when `auoreo.backends.delivery` is not installed."""

    ok: bool
    detail: str = ""
    external_id: str | None = None


def delivery_module() -> Any | None:
    try:
        return importlib.import_module(DELIVERY_MODULE)
    except ImportError:
        return None


class PluginDelivery:
    """`DeliveryProvider`-shaped adapter: `send(target, text, *, meta)` → plugin `delivery.send`."""

    def __init__(self, plugin: str, name: str, sender: Sender) -> None:
        self.plugin = plugin
        self.name = name
        self._sender = sender

    async def send(self, target: str, text: str, *, meta: dict[str, Any] | None = None) -> Any:
        try:
            res = await self._sender(self.name, target, text, dict(meta or {}))
        except Exception as exc:  # a dead plugin is a failed delivery, not a crash
            res = {"ok": False, "detail": str(exc), "external_id": None}
        return make_receipt(bool(res.get("ok")), str(res.get("detail", "")), res.get("external_id"))

    async def check(self) -> Any:
        return make_receipt(True, f"plugin {self.plugin}")


def make_receipt(ok: bool, detail: str, external_id: Any = None) -> Any:
    mod = delivery_module()
    ext = None if external_id is None else str(external_id)
    cls = getattr(mod, "DeliveryReceipt", None) if mod is not None else None
    if cls is not None:
        try:
            return cls(ok=ok, detail=detail, external_id=ext)
        except TypeError:
            log.debug("DeliveryReceipt signature differs; using the local receipt")
    return Receipt(ok, detail, ext)


def _provider(adapter: PluginDelivery, mod: Any) -> Any:
    base = getattr(mod, "DeliveryProvider", None)
    if base is None or isinstance(adapter, base):
        return adapter
    try:  # make `isinstance(p, DeliveryProvider)` hold when the base is an ABC
        cls = type("PluginDeliveryProvider", (PluginDelivery, base), {})
        return cls(adapter.plugin, adapter.name, adapter._sender)
    except TypeError:
        return adapter


def register_delivery(adapter: PluginDelivery) -> bool:
    """Register in the shared delivery registry; False when that module is absent."""
    mod = delivery_module()
    if mod is None or not hasattr(mod, "register"):
        return False
    try:
        mod.register(adapter.name, _provider(adapter, mod))
    except Exception:
        log.exception("could not register delivery '%s'", adapter.name)
        return False
    return True


def unregister_delivery(name: str) -> None:
    mod = delivery_module()
    if mod is not None and hasattr(mod, "unregister"):
        try:
            mod.unregister(name)
        except Exception:
            log.debug("delivery '%s' was not registered", name)


# ------------------------------------------------------------------ skills
@dataclass(frozen=True)
class PluginSkill:
    plugin: str
    name: str
    description: str
    body: str


def parse_skills(plugin: str, raw: list[Any]) -> list[PluginSkill]:
    out: list[PluginSkill] = []
    for item in raw:
        if isinstance(item, dict) and item.get("name"):
            out.append(
                PluginSkill(
                    plugin, str(item["name"]), str(item.get("description", "")), str(item.get("body", ""))
                )
            )
    return out
