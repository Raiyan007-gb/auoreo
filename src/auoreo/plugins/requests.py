"""Plugin → host requests (JSON-RPC methods a plugin may call). Every call is checked here:
secrets only when declared in `consumes`, audit rows namespaced to the plugin, usage only with
`consumes: [usage]`. Kept out of `host.py` for the 500-line cap."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from auoreo.core.redaction import redact_obj
from auoreo.gateway.types import Usage
from auoreo.plugins.connectors import PairingStore
from auoreo.plugins.grants import secret_granted
from auoreo.plugins.protocol import RpcError

if TYPE_CHECKING:
    from auoreo.plugins.host import PluginHost, PluginState

ACTION_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
DETAIL_MAX = 4096
MAX_COST_USD = 100.0  # per call; a plugin cannot flood budgets with absurd values

Handler = Callable[["PluginHost", "PluginState", dict[str, Any]], Awaitable[Any]]


async def secret_get(host: PluginHost, st: PluginState, p: dict[str, Any]) -> Any:
    name = str(p.get("name", ""))
    allowed = secret_granted(name, st.manifest.consumes)
    await host.s.audit.record(
        actor=f"plugin:{st.name}",
        action="plugin.secret",
        decision="allow" if allowed else "deny",
        detail={"secret": name},
    )
    if not allowed:
        raise RpcError(f"secret '{name}' is not declared in consumes", code=-32001)
    return {"value": host.s.secrets.get(name)}


async def pairing_request(host: PluginHost, st: PluginState, p: dict[str, Any]) -> Any:
    store = PairingStore(host.home, st.name)
    chat_id = str(p.get("chat_id", ""))
    if chat_id in store.allowlist(list(st.config.get("allowlist", []))):
        return {"allowed": True}
    code = store.request(chat_id, str(p.get("user", "")), str(p.get("name", "")))
    await host.s.emit(
        "server", "plugin.pairing_requested", {"name": st.name, "chat_id": chat_id, "user": p.get("user")}
    )
    return {"allowed": False, "code": code}


async def audit_record(host: PluginHost, st: PluginState, p: dict[str, Any]) -> Any:
    """Append an audit row `plugin.<name>.<action>`; detail is redacted and size-capped."""
    action = str(p.get("action", ""))
    if not ACTION_RE.match(action):
        raise RpcError("action must match ^[a-z][a-z0-9_.-]{0,63}$", code=-32602)
    detail = p.get("detail") if isinstance(p.get("detail"), dict) else {}
    if len(json.dumps(detail, default=str)) > DETAIL_MAX:
        raise RpcError(f"detail exceeds {DETAIL_MAX} bytes", code=-32602)
    await host.s.audit.record(
        actor=f"plugin:{st.name}",
        action=f"plugin.{st.name}.{action}",
        decision=str(p.get("decision", ""))[:32],
        detail=redact_obj(detail),
    )
    return {"ok": True}


async def usage_record(host: PluginHost, st: PluginState, p: dict[str, Any]) -> Any:
    """Record spend in the usage ledger (counts toward session budgets and `auoreo stats`)."""
    if "usage" not in st.manifest.consumes:
        raise RpcError("usage.record needs `consumes: [usage]` in plugin.yaml", code=-32001)
    session_id = str(p.get("session_id") or f"plugin:{st.name}")
    try:
        cost = float(p.get("cost_usd") or 0.0)
        prompt, completion = int(p.get("prompt_tokens") or 0), int(p.get("completion_tokens") or 0)
    except (TypeError, ValueError) as exc:
        raise RpcError(f"bad usage values: {exc}", code=-32602) from exc
    if not 0.0 <= cost <= MAX_COST_USD or prompt < 0 or completion < 0:
        raise RpcError(f"cost_usd must be within 0..{MAX_COST_USD} and tokens ≥ 0", code=-32602)
    model = f"plugin:{st.name}/{str(p.get('model') or 'unknown')[:80]}"
    usage = Usage(prompt, completion, cost_usd=cost, estimated=bool(p.get("estimated", True)))
    await host.s.coordinator.runner.usage.record(session_id, f"plugin-{st.name}", model, usage)
    return {"ok": True}


async def oauth_token(host: PluginHost, st: PluginState, p: dict[str, Any]) -> Any:
    """I7: a short-lived access token for `oauth/<connector>` (needs `consumes: ["oauth:<name>"]`).
    The refresh token never leaves the host; the access token comes from the in-memory cache."""
    from auoreo.core import oauth_login as oauth

    connector = str(p.get("connector", ""))
    allowed = f"oauth:{connector}" in st.manifest.consumes
    await host.s.audit.record(
        actor=f"plugin:{st.name}",
        action="plugin.oauth",
        decision="allow" if allowed else "deny",
        detail={"connector": connector[:64]},
    )
    if not allowed:
        raise RpcError(f"oauth '{connector}' is not declared in consumes", code=-32001)
    try:
        token = await oauth.tokens_for(host.s).access_token(connector)
    except oauth.OAuthError as exc:
        raise RpcError(str(exc), code=-32002) from exc
    return {"access_token": token}


HOST_REQUESTS: dict[str, Handler] = {
    "secret.get": secret_get,
    "pairing.request": pairing_request,
    "audit.record": audit_record,
    "usage.record": usage_record,
    "oauth.token": oauth_token,
}
