"""The per-host `ssh` permission gate and the `net.ssh*` audit rows (M7 hosts-a, D1).

SSH is a raw socket from the harness process, so the egress proxy never sees it. With
`settings.m7.ssh.hosts` on, a session's first use of an SSH environment (a `hosts/<name>.yaml`
host, a legacy `settings.environments` entry, or the default backend when that is `ssh`) must
pass permission `ssh` with the environment name as the pattern (`default` for the default
backend). The grant has `net_direct` semantics:

- only an explicit `allow` rule or a human reply grants; `yolo`, `acceptEdits` and `smart`
  never do, and an `always` reply is not remembered;
- a human grant needs a justification (kept 24 h, hash in the audit row);
- it lasts for the session (`SessionState.ssh_granted`), is not restored after a restart, and
  is keyed by the target, so editing the host definition asks again. A denial is not cached.

Audit: `net.ssh_grant{mode, name, host, port, grant_id, justificationHash}` at grant time and
`net.ssh{mode, name, host, port, grant_id}` on every socket open (pooled reconnects included).
This is a documented deviation from "tools reach the network only through the egress proxy"
(`docs/environments.md`); the two rows are the compensating record.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from auoreo.core import justifications
from auoreo.core.hosts import hosts_enabled
from auoreo.tools.context import ToolContext, ToolError

PERMISSION = "ssh"
DEFAULT_NAME = "default"
JUSTIFICATION_ARG = "justification"
_GENERIC = frozenset({"need access", "need ssh", "ssh access", "required", "necessary", "please"})

OPENER: ContextVar[tuple[str, str | None, str | None]] = ContextVar(
    "auoreo_ssh_opener", default=("system", None, None)
)
"""`(actor, session_id, grant_id)` a socket opened in this task is attributed to."""

Record = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class SshTarget:
    name: str
    host: str
    port: int
    user: str
    key_secret: str

    @property
    def cache_key(self) -> str:
        raw = f"{self.host}\n{self.port}\n{self.user}\n{self.key_secret}"
        return f"{self.name}:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


def is_ssh(backend: Any) -> bool:
    return getattr(backend, "name", None) == "ssh" and hasattr(backend, "net_audit")


def target_of(name: str, backend: Any) -> SshTarget | None:
    cfg = getattr(backend, "cfg", None) if is_ssh(backend) else None
    if cfg is None:
        return None
    return SshTarget(name, str(cfg.host), int(cfg.port), str(cfg.username), str(cfg.key_secret or ""))


def attach(
    backend: Any, settings: Any, record: Record, name: str, *, actor: str | None = None, always: bool = False
) -> None:
    """Give an ssh backend its `net.ssh` audit callable (once). `actor` fixes the attribution
    (`operator` for doctor and the CLI); otherwise the task's `OPENER` decides. `always`
    records with the flag off too (the CLI, which works without it)."""
    if not is_ssh(backend) or backend.net_audit is not None:
        return

    async def audit(detail: dict[str, Any]) -> None:
        if not always and not hosts_enabled(settings):
            return
        who, session_id, grant_id = (actor, None, None) if actor else OPENER.get()
        await record(
            actor=who,
            action="net.ssh",
            session_id=session_id,
            decision="allow",
            detail={**detail, "name": name, "grant_id": grant_id},
        )

    backend.net_audit = audit


def justification_error(text: str | None) -> str | None:
    j = " ".join((text or "").split())
    if not j:
        return f"needs a `{JUSTIFICATION_ARG}` argument (why this session must reach the host)"
    if not 10 <= len(j) <= 500:
        return f"{JUSTIFICATION_ARG} must be 10–500 characters"
    if j.lower().strip(" .!") in _GENERIC:
        return f"{JUSTIFICATION_ARG} is generic; say what will be done on the host"
    return None


async def gate_ssh(
    ctx: ToolContext, target: SshTarget | None, *, tool: str, justification: str | None = None
) -> str | None:
    """Authorize this session's use of `target`; returns the grant id (None when the gate is
    off or the backend is not SSH). Raises `ToolError` unless allowed."""
    svc = ctx.services.get("services")
    if target is None or svc is None or not hosts_enabled(svc.settings):
        return None
    if (grant := ctx.state.ssh_granted.get(target.cache_key)) is not None:
        OPENER.set(("agent", ctx.session_id, grant))
        return grant
    engine = ctx.services.get("engine")
    if engine is None:  # fail closed: this gate is the only check on the socket
        raise ToolError(f"ssh host '{target.name}' needs the `ssh` permission; no permission engine attached")
    decision = engine.evaluate(PERMISSION, target.name, "ask", interactive=ctx.interactive)
    by_rule = decision.action == "allow" and decision.reason != "mode"
    text = " ".join(justification.split()) if isinstance(justification, str) else ""
    asks_human = not by_rule and decision.action != "deny" and ctx.interactive
    if asks_human and (why := justification_error(text)) is not None:
        raise ToolError(
            f"ssh host '{target.name}' {why}: call use_environment with name "
            f"'{target.name}' and a {JUSTIFICATION_ARG} (a human reviews it)"
        )
    detail = f"ssh {target.user}@{target.host}:{target.port} ({target.name})"
    await ctx.authorize(
        tool, PERMISSION, target.name, "ask", sticky=False, detail=detail, justification=text or None
    )
    grant = uuid.uuid4().hex
    digest = None
    if text and justification_error(text) is None:
        ttl_h = svc.settings.i9.net_direct.justification_ttl_h
        digest = justifications.store(svc.settings.home, grant, text, kind="ssh", tool=tool, ttl_h=ttl_h)
    await svc.audit.record(
        actor="agent",
        action="net.ssh_grant",
        session_id=ctx.session_id,
        decision="allow",
        reason=decision.detail if by_rule else "human",
        detail={
            "mode": "ssh",
            "name": target.name,
            "host": target.host,
            "port": target.port,
            "grant_id": grant,
            "justificationHash": digest,
            "tool": tool,
        },
    )
    ctx.state.ssh_granted[target.cache_key] = grant
    OPENER.set(("agent", ctx.session_id, grant))
    return grant
