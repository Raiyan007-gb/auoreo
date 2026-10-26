"""Approve / deny from a messaging connector (spec §19.9): `/approve`, `/approve <id>`,
`/approve all`, `/deny [reason]`, `/deny <id> [reason]`, `/deny all [reason]`, `/pending`.

- Only a paired owner identity may answer: the chat must be on the plugin's allowlist (paired)
  and, when `plugins.<name>.owners` is set, listed there too. Anyone else gets a refusal and
  the attempt is audited.
- Without an id the oldest pending request is answered (FIFO across sessions); an id is
  matched by prefix (≥ 4 characters) against pending request ids.
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


def describe(p: Pending) -> str:
    pattern = p.pattern if len(p.pattern) <= 200 else p.pattern[:200] + "…"
    return f"[{p.request_id[:8]}] {p.tool} ({p.permission}): {pattern}"


def _targets(pending: list[Pending], words: list[str]) -> tuple[list[Pending], list[str]]:
    """(requests to answer, remaining words = the reason)."""
    if words and words[0].lower() == "all":
        return pending, words[1:]
    if words and len(words[0]) >= MIN_ID:
        hits = [p for p in pending if p.request_id.startswith(words[0].lower())]
        if hits:
            return hits[:1], words[1:]
    return pending[:1], words


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
    if cmd == "/pending":
        if not pending:
            return "No approvals are pending."
        return "Pending approvals (oldest first):\n" + "\n".join(describe(p) for p in pending[:20])
    if not pending:
        return "No approvals are pending."
    chosen, rest = _targets(pending, words[1:])
    reply: Reply = "once" if cmd == "/approve" else "reject"
    reason = (" ".join(rest)[:500] or None) if reply == "reject" else None
    done: list[str] = []
    for p in chosen:
        engine_for = services.coordinator.engine_for
        if await reply_with_grants(services.approvals, engine_for, p.request_id, reply, reason):
            done.append(describe(p))
        await services.audit.record(
            actor=actor,
            action="approval.chat",
            session_id=p.session_id,
            decision=reply,
            reason=reason or "",
            detail={"chat_id": chat_id[:64], "request_id": p.request_id},
        )
    verb = "Approved" if reply == "once" else "Denied"
    return f"{verb} {len(done)}:\n" + "\n".join(done) if done else "Nothing was answered (already resolved)."


class ApprovalNotifier:
    """Push each new approval request of `session_id` to the chat while a run is in flight."""

    def __init__(self, services: Services, session_id: str, chat_id: str, send: Send) -> None:
        self.s = services
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
                text = (
                    f"Approval needed {describe(p)}\n"
                    f"Reply /approve {p.request_id[:8]} or /deny {p.request_id[:8]} [reason]."
                )
                with contextlib.suppress(Exception):
                    await self.send(self.chat_id, text, {"session_id": self.session_id})
            await asyncio.sleep(POLL_S)
