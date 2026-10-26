"""Plugin-provided webhook providers (`provides: [webhook_provider]`, spec §15 L650-659).

A plugin advertises `webhook_providers: ["<kind>", …]` from `initialize`. For each kind the
host registers a proxy in `automation.webhook_providers`; core keeps routing, caps, rate
limits, idempotency and delivery, and only the provider's part crosses the process boundary:

    webhook.verify {kind, secret, headers, body_b64, now} → {ok, reason?, event?, delivery_id?, identity?}
    webhook.parse  {kind, payload, headers}               → {payload}   (optional)

The secret of the route goes to the plugin that owns the provider (it owns the verify math);
the plugin's runtime implements `webhook_verify(...)` / `webhook_parse(...)`. A plugin that is
not running, times out or errors makes `verify` fail (401), never 500.
"""

from __future__ import annotations

import base64
import logging
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from auoreo.automation import webhook_providers as registry
from auoreo.automation.webhook_providers import Verdict
from auoreo.plugins.protocol import RpcError

if TYPE_CHECKING:
    from auoreo.plugins.host import PluginHost, PluginState

log = logging.getLogger(__name__)
KIND_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
VERIFY_TIMEOUT_S = 10.0


class PluginWebhookProvider:
    def __init__(self, st: PluginState, kind: str) -> None:
        self.st = st
        self.kind = kind

    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        peer = self.st.peer
        if peer is None or not peer.alive:
            raise RpcError(f"plugin '{self.st.name}' is not running")
        return await peer.request(method, {"kind": self.kind, **params}, timeout=VERIFY_TIMEOUT_S)

    async def verify(self, secret: str, headers: Mapping[str, str], body: bytes, now: float) -> Verdict:
        params = {
            "secret": secret,
            "headers": dict(headers),
            "body_b64": base64.b64encode(body).decode(),
            "now": now,
        }
        try:
            res = await self._request("webhook.verify", params)
        except RpcError as exc:
            return Verdict(False, f"provider {self.kind}: {exc}"[:200])
        res = res if isinstance(res, dict) else {}

        def text(key: str) -> str | None:
            value = res.get(key)
            return str(value)[:256] if value not in (None, "") else None

        return Verdict(
            bool(res.get("ok")), text("reason") or "", text("event"), text("delivery_id"), text("identity")
        )

    def event_name(self, headers: Mapping[str, str], payload: dict[str, Any]) -> str:
        ev = payload.get("event")
        return ev if isinstance(ev, str) else ""

    def delivery_id(self, headers: Mapping[str, str], body: bytes, payload: dict[str, Any]) -> str | None:
        return None

    async def parse(self, payload: dict[str, Any], headers: Mapping[str, str]) -> dict[str, Any]:
        try:
            res = await self._request("webhook.parse", {"payload": payload, "headers": dict(headers)})
        except RpcError:
            return payload  # optional: a plugin without `webhook_parse` keeps the payload as is
        out = res.get("payload") if isinstance(res, dict) else None
        return out if isinstance(out, dict) else payload


def register_providers(host: PluginHost, st: PluginState, info: dict[str, Any]) -> list[str]:
    """Register the kinds a plugin advertised (only with `provides: [webhook_provider]`)."""
    unregister_providers(st)
    kinds = [str(k) for k in info.get("webhook_providers", []) if isinstance(k, str)]
    if kinds and "webhook_provider" not in st.manifest.provides:
        log.warning("plugin %s advertises webhook providers without provides: [webhook_provider]", st.name)
        return []
    done: list[str] = []
    for kind in kinds:
        if not KIND_RE.fullmatch(kind):
            log.warning("plugin %s: invalid webhook provider kind %r", st.name, kind)
            continue
        if (why := registry.register(PluginWebhookProvider(st, kind), st.name)) is not None:
            log.warning("plugin %s: %s", st.name, why)
            continue
        done.append(kind)
    st.trust["webhook_providers"] = done
    return done


def unregister_providers(st: PluginState) -> None:
    for kind in st.trust.pop("webhook_providers", []) or []:
        registry.unregister(kind, st.name)


__all__ = ["PluginWebhookProvider", "register_providers", "unregister_providers"]
