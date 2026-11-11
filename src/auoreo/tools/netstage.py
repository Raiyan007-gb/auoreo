"""Executor stage: per-tool egress (`net: none|proxy|direct`, spec §25.10 L1017/L1021).

Runs after the permission decision (and any sandbox escalation), before the handler:

- `none`   — nothing to set up; the net helpers (`auoreo.tools.net.egress_client`) refuse to
             open connections for such a call, sandboxes unshare the network.
- `proxy`  — a per-call token is minted on the loopback egress proxy (`tools/egress.py`);
             the call's `ExecPolicy.proxy_url` and child env (`HTTPS_PROXY`/`HTTP_PROXY`) point
             at it. When the call ends the token is revoked and the tunnels it opened
             (host/port/verdict) are written to the audit row.
- `direct` — never a default: the call must carry a `net_justification` argument (10–500
             chars, not a generic plea), permission `net_direct` with pattern `<tool>:<host>`
             is evaluated (default `ask`, never sticky, `yolo` does not pre-grant it), and a
             one-shot `grantId` is minted and audited (`net.direct_grant`). The plaintext
             justification is kept for 24 h in `<home>/egress/justifications.json`; the audit
             row only has its hash.

Audit shape (every tool row): `net{mode, host, port, grant_id}` (+ `conns` for proxy calls).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from auoreo.core import justifications
from auoreo.tools.egress import EgressProxy, proxy_of
from auoreo.tools.stages import policy_context

if TYPE_CHECKING:
    from auoreo.core.permissions import PermissionEngine
    from auoreo.gateway.types import ToolCall
    from auoreo.harness.loader import ToolDef
    from auoreo.tools.context import ToolContext
    from auoreo.tools.executor import ToolExecutor, ToolOutcome

JUSTIFICATION_ARG = "net_justification"
_GENERIC = frozenset(
    {"need access", "need network", "network access", "internet access", "required", "necessary", "please"}
)
_URL_ARGS = ("url", "endpoint", "uri")
_HOST_ARGS = ("host", "hostname")


@dataclass
class Egress:
    """What the net stage set up for one call; `finish()` returns the audit `net` value."""

    mode: str
    host: str | None = None
    port: int | None = None
    proxy_url: str | None = None
    grant_id: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    _proxy: EgressProxy | None = None
    _token: str | None = None
    _done: dict[str, Any] | None = None

    def audit(self) -> dict[str, Any]:
        return {"mode": self.mode, "host": self.host, "port": self.port, "grant_id": self.grant_id}

    def finish(self) -> dict[str, Any]:
        if self._done is not None:
            return self._done
        out = self.audit()
        if self._proxy is not None and self._token is not None:
            entry = self._proxy.revoke(self._token)
            self._token = None
            conns = entry.conns if entry is not None else []
            out["conns"] = conns[:50]
            if conns and self.host is None:
                out["host"], out["port"] = conns[0]["host"], conns[0]["port"]
        self._done = out
        return out


def target_of(args: dict[str, Any]) -> tuple[str | None, int | None]:
    """Best-effort destination from the call's arguments (`url`/`host`/`port`)."""
    for key in _URL_ARGS:
        if isinstance(args.get(key), str):
            parts = urlsplit(args[key])
            if parts.hostname:
                try:
                    port = parts.port or (443 if parts.scheme == "https" else 80)
                except ValueError:
                    port = None
                return parts.hostname.lower(), port
    for key in _HOST_ARGS:
        if isinstance(args.get(key), str):
            port = args.get("port")
            return args[key].lower(), port if isinstance(port, int) else None
    return None, None


def proxy_env(url: str) -> dict[str, str]:
    """Child-process env that routes HTTP(S) through the egress proxy."""
    return {"HTTPS_PROXY": url, "HTTP_PROXY": url, "https_proxy": url, "http_proxy": url, "NO_PROXY": ""}


def split_justification(tool: ToolDef, args: dict[str, Any], logged: dict[str, Any]) -> str | None:
    """Pop `net_justification` out of the arguments (the handler and hooks never see it)."""
    logged.pop(JUSTIFICATION_ARG, None)
    value = args.pop(JUSTIFICATION_ARG, None)
    if tool.manifest.net != "direct":
        return None
    return value if isinstance(value, str) else None


def justification_error(text: str | None) -> str | None:
    j = " ".join((text or "").split())
    if not j:
        return f"`net: direct` needs a `{JUSTIFICATION_ARG}` argument (why this call must bypass the proxy)"
    if not 10 <= len(j) <= 500:
        return f"{JUSTIFICATION_ARG} must be 10–500 characters"
    if j.lower().strip(" .!") in _GENERIC:
        return f"{JUSTIFICATION_ARG} is generic; name the destination and why the proxy cannot be used"
    return None


async def net_stage(
    ex: ToolExecutor,
    call: ToolCall,
    tool: ToolDef,
    args: dict[str, Any],
    ctx: ToolContext,
    engine: PermissionEngine,
    audit: dict[str, Any],
    justification: str | None,
) -> tuple[ToolOutcome | None, Egress]:
    host, port = target_of(args)
    eg = Egress(tool.manifest.net, host=host, port=port)
    if eg.mode == "proxy":
        proxy = proxy_of(ex.s)
        if proxy is not None:
            eg._proxy, eg._token = proxy, proxy.mint(f"{tool.name}:{call.id}")
            eg.proxy_url = proxy.url(eg._token)
            eg.env = proxy_env(eg.proxy_url)
        return None, eg
    if eg.mode != "direct":
        return None, eg
    if (why := justification_error(justification)) is not None:
        return ex._err(call, f"refused: {why}"), eg
    assert justification is not None
    pattern = f"{tool.name}:{host or '*'}"
    decision = engine.evaluate(
        "net_direct", pattern, "ask", interactive=ctx.interactive, context=policy_context(tool, ctx, args)
    )
    if decision.action == "deny":
        return ex._err(call, f"direct network access denied ({decision.detail}). Do not retry unchanged."), eg
    if decision.action == "ask" or (decision.reason == "mode" and decision.note == "yolo"):
        refused, _ = await ex._ask(
            call, tool, args, ctx, engine, "net_direct", pattern, audit, decision.detail, sticky=False
        )
        if refused is not None:
            return refused, eg
    eg.grant_id = uuid.uuid4().hex
    st = ex.s.settings
    digest = justifications.store(
        st.home,
        eg.grant_id,
        justification,
        kind="net_direct",
        tool=tool.name,
        ttl_h=st.i9.net_direct.justification_ttl_h,
    )
    await ex.s.audit.record(
        actor="agent",
        action="net.direct_grant",
        session_id=ctx.session_id,
        decision="allow",
        reason=decision.detail,
        detail={**audit, "net": eg.audit(), "justificationHash": digest, "grantId": eg.grant_id},
    )
    return None, eg
