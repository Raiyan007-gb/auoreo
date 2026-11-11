"""Approve / deny from a messaging connector (spec §19.9): `/approve`, `/approve <id>`,
`/approve all`, `/deny [reason]`, `/deny <id> [reason]`, `/deny all [reason]`, `/pending`.

- Only a paired owner identity may answer: the chat must be on the plugin's allowlist (paired)
  and, when `plugins.<name>.owners` is set, listed there too. Anyone else gets a refusal and
  the attempt is audited.
- Without an id the oldest pending request is answered (FIFO across sessions); an id is
  matched by prefix (≥ 4 characters) against pending request ids.
- One-shot requests (an `ssh` host, `net_direct`, sandbox escalation, a literal secret) are
  approved only by their explicit id: `/approve` without an id and `/approve all` skip them
  and say so. `/deny` still answers them either way.
- M7: a `local_only` request (`ssh`/`sftp` while `m7.ssh.chat_grants` is off) is never
  approved from a chat, not even by its id; the refusal is audited (`approval.chat`,
  `decision: deny`, `reason: local_only`). `/deny` still answers it.
- With `teams2.chat_approvals` the connector's own runs are interactive, and every approval
  request of that chat's session is pushed to the chat (`ApprovalNotifier`).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from auoreo.core.approvals import Pending, Reply, reply_with_grants
from auoreo.plugins.connectors import PairingStore, file_config

if TYPE_CHECKING:
    from auoreo.core.services import Services

Send = Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]]
COMMANDS = ("/approve", "/deny", "/pending")
MIN_ID = 4
POLL_S = 0.25


def is_command(text: str) -> bool:
    head = text.strip().split(maxsplit=1)[0].lower() if text.strip() else ""
    return head.split("@", 1)[0] in COMMANDS  # Telegram appends `@botname` in groups


def is_owner(services: Services, plugin: str, chat_id: str) -> bool:
    home = services.settings.home
    if chat_id not in PairingStore(home, plugin).allowlist():
        return False
    owners = file_config(home, plugin).get("owners")
    return not isinstance(owners, list) or not owners or chat_id in {str(o) for o in owners}


SSH_PERMISSIONS = frozenset({"ssh", "sftp"})
LOCAL_ONLY = (
    "SSH host and file-transfer requests are answered in the web UI only "
    "(or allowed by a permission rule); /deny still works here."
)


def _clip(text: str, n: int = 200) -> str:
    return text if len(text) <= n else text[:n] + "…"


def describe(p: Pending, agent: str = "") -> str:
    """`agent` (M7): named when the connector routes one chat to several agents. An `ssh`
    card shows the `user@host:port` it opens and, like any one-shot card, the justification."""
    shown = p.detail if p.permission in SSH_PERMISSIONS and p.detail else p.pattern
    who = f"{agent}: " if agent else ""
    line = f"[{p.request_id[:8]}] {who}{p.tool} ({p.permission}): {_clip(shown)}"
    if p.justification:
        line += f"\n  justification: {_clip(' '.join(p.justification.split()), 500)}"
    return line


def _agents(services: Services, plugin: str) -> dict[str, str]:
    """`session id → agent` when the connector has `routes`, else {} (prompts stay as they were)."""
    home = services.settings.home
    cfg = {**services.settings.plugins.get(plugin, {}), **file_config(home, plugin)}
    if not cfg.get("routes"):
        return {}
    from auoreo.plugins.routes import session_agents

    fixed = str(cfg.get("agent") or services.settings.agent)
    return session_agents(home / "plugins" / plugin / "sessions.json", fixed)


def _targets(
    pending: list[Pending], words: list[str], approve: bool
) -> tuple[list[Pending], list[str], list[Pending]]:
    """(requests to answer, remaining words = the reason, one-shot requests skipped). An
    approval answers a one-shot request only when its id is named."""
    if words and len(words[0]) >= MIN_ID and words[0].lower() != "all":
        hits = [p for p in pending if p.request_id.startswith(words[0].lower())]
        if hits:
            return hits[:1], words[1:], []
    skipped = [p for p in pending if p.one_shot] if approve else []
    pool = [p for p in pending if p not in skipped]
    if words and words[0].lower() == "all":
        return pool, words[1:], skipped
    return pool[:1], words, skipped


async def handle_command(services: Services, plugin: str, chat_id: str, text: str) -> str:
    """Run one approval command from `chat_id`; returns the reply for the chat."""
    words = text.strip().split()
    cmd = words[0].lower().split("@", 1)[0]
    actor = f"connector:{plugin}"
    if not is_owner(services, plugin, chat_id):
        await services.audit.record(
            actor=actor,
            action="approval.chat",
            decision="deny",
            reason="not a paired owner",
            detail={"chat_id": chat_id[:64], "command": cmd},
        )
        return "Only a paired owner of this AUOREO server can answer approvals."
    pending = services.approvals.pending()
    names = _agents(services, plugin)
    if cmd == "/pending":
        if not pending:
            return "No approvals are pending."
        lines = [describe(p, names.get(p.session_id, "")) for p in pending[:20]]
        return "Pending approvals (oldest first):\n" + "\n".join(lines)
    if not pending:
        return "No approvals are pending."
    reply: Reply = "once" if cmd == "/approve" else "reject"
    local = [p for p in pending if p.local_only] if reply == "once" else []
    if local:
        # M7: `ssh`/`sftp` cards are answered in the web UI only (`m7.ssh.chat_grants` off)
        named = words[1].lower() if len(words) > 1 and len(words[1]) >= MIN_ID else ""
        refused = [p for p in local if named and p.request_id.startswith(named)]
        for p in refused:
            await services.audit.record(
                actor=actor,
                action="approval.chat",
                session_id=p.session_id,
                decision="deny",
                reason="local_only",
                detail={"chat_id": chat_id[:64], "request_id": p.request_id, "permission": p.permission},
            )
        if refused:
            return f"Not approved: {describe(refused[0], names.get(refused[0].session_id, ''))}\n{LOCAL_ONLY}"
        pending = [p for p in pending if not p.local_only]
        if not pending:
            return f"Nothing was approved. {len(local)} pending request(s): {LOCAL_ONLY}"
    chosen, rest, skipped = _targets(pending, words[1:], reply == "once")
    reason = (" ".join(rest)[:500] or None) if reply == "reject" else None
    done: list[str] = []
    for p in chosen:
        engine_for = services.coordinator.engine_for
        if await reply_with_grants(services.approvals, engine_for, p.request_id, reply, reason):
            done.append(describe(p, names.get(p.session_id, "")))
        await services.audit.record(
            actor=actor,
            action="approval.chat",
            session_id=p.session_id,
            decision=reply,
            reason=reason or "",
            detail={"chat_id": chat_id[:64], "request_id": p.request_id},
        )
    verb = "Approved" if reply == "once" else "Denied"
    if done:
        out = f"{verb} {len(done)}:\n" + "\n".join(done)
    else:
        out = "Nothing was approved." if skipped else "Nothing was answered (already resolved)."
    if skipped:
        out += f"\nNot approved ({len(skipped)} one-shot, approve each with /approve <id>):\n" + "\n".join(
            describe(p, names.get(p.session_id, "")) for p in skipped
        )
    if local:
        out += f"\nNot approved ({len(local)}): {LOCAL_ONLY}"
    return out


class ApprovalNotifier:
    """Push each new approval request of `session_id` to the chat while a run is in flight."""

    def __init__(
        self, services: Services, session_id: str, chat_id: str, send: Send, *, agent: str = ""
    ) -> None:
        self.s = services
        self.agent = agent
        self.session_id = session_id
        self.chat_id = chat_id
        self.send = send
        self.seen: set[str] = set()
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> ApprovalNotifier:
        self._task = asyncio.create_task(self._loop(), name=f"chat-approvals:{self.session_id}")
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    async def _loop(self) -> None:
        while True:
            for p in self.s.approvals.pending(self.session_id):
                if p.request_id in self.seen:
                    continue
                self.seen.add(p.request_id)
                how = (
                    f"{LOCAL_ONLY} Reply /deny {p.request_id[:8]} [reason] to refuse."
                    if p.local_only
                    else f"Reply /approve {p.request_id[:8]} or /deny {p.request_id[:8]} [reason]."
                )
                text = f"Approval needed {describe(p, self.agent)}\n{how}"
                with contextlib.suppress(Exception):
                    await self.send(self.chat_id, text, {"session_id": self.session_id})
            await asyncio.sleep(POLL_S)
