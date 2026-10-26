"""Agent-to-agent mailbox (spec §16.4): append-only rows; `text` mail is delivered as a
`<mail from=… summary=…>` user message at the recipient's next step (inbox `inject`).

Broadcast (`to="*"`) fans out one row per other member, needs a summary of ≤10 words and
is rate-limited per team (spam guard, audited).
"""

from __future__ import annotations

import time
from collections import deque
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from auoreo.core.db import MailboxRow
from auoreo.teams.format import mail_envelope

if TYPE_CHECKING:
    from auoreo.teams.service import TeamsService

LEAD = "lead"
BROADCAST = "*"
BROADCAST_PER_MIN = 5
BROADCAST_SUMMARY_WORDS = 10
MAIL_TYPES = (
    "text",
    "shutdown_request",
    "shutdown_response",
    "plan_approval_response",
    "permission_request",
    "permission_response",
    "idle_notification",
    "team_permission_update",  # --- I3: leader→worker control (teams.control_msgs)
    "mode_set_request",
)


class TeamsError(ValueError):
    """A model-visible refusal (depth limit, name taken, unknown peer, rate limit, …)."""


class Mailbox:
    def __init__(self, teams: TeamsService) -> None:
        self.t = teams
        self._broadcasts: dict[str, deque[float]] = {}

    def _rate_limit(self, team_id: str) -> None:
        now = time.monotonic()
        window = self._broadcasts.setdefault(team_id, deque())
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= BROADCAST_PER_MIN:
            raise TeamsError(f"broadcast rate limit: at most {BROADCAST_PER_MIN} per minute per team")
        window.append(now)

    async def send(
        self,
        team_id: str,
        sender: str,
        to: str,
        text: str,
        summary: str = "",
        type: str = "text",
        request_id: str | None = None,
    ) -> list[MailboxRow]:
        """Store and deliver mail. Returns one row per recipient."""
        team = await self.t.get_team(team_id)
        if team is None:
            raise TeamsError(f"unknown team '{team_id}'")
        if type not in MAIL_TYPES:
            raise TeamsError(f"unknown mail type '{type}' (known: {', '.join(MAIL_TYPES)})")
        if to == BROADCAST:
            if not summary.strip() or len(summary.split()) > BROADCAST_SUMMARY_WORDS:
                raise TeamsError(f"broadcast needs a summary of 1–{BROADCAST_SUMMARY_WORDS} words")
            recipients = [p["name"] for p in await self.t.peers(team_id) if p["name"] != sender]
        else:
            if to == sender:
                raise TeamsError("cannot send mail to yourself")
            await self.t.session_for(team, to)  # validates the name
            recipients = [to]
        if to == BROADCAST:
            self._rate_limit(team_id)
            await self.t.s.audit.record(
                actor="agent",
                action="mail.broadcast",
                session_id=await self.t.session_for(team, sender),
                decision="allow",
                detail={"team": team_id, "from": sender, "recipients": recipients, "summary": summary},
            )
        rows: list[MailboxRow] = []
        for name in recipients:
            row = MailboxRow(
                team_id=team_id,
                recipient=name,
                sender=sender,
                type=type,
                summary=summary[:200],
                text=text,
                request_id=request_id,
            )
            async with self.t.s.db.session() as s:
                s.add(row)
                await s.commit()
            if type == "text":  # structured types stay queued for their consumers
                await self.t.inject(
                    await self.t.session_for(team, name), mail_envelope(sender, summary, text)
                )
                await self.mark_read(row.id)
                row.read = True
            await self.t.s.emit(
                "server",
                "mailbox.message",
                {"team": team_id, "from": sender, "to": name, "summary": summary, "id": row.id},
            )
            rows.append(row)
        return rows

    async def mark_read(self, mail_id: int) -> None:
        async with self.t.s.db.session() as s:
            row = await s.get(MailboxRow, mail_id)
            if row is not None:
                row.read = True
                await s.commit()

    async def messages(self, team_id: str, member: str | None = None, limit: int = 500) -> list[MailboxRow]:
        async with self.t.s.db.session() as s:
            q = select(MailboxRow).where(MailboxRow.team_id == team_id).order_by(MailboxRow.id).limit(limit)
            if member:
                q = q.where((MailboxRow.recipient == member) | (MailboxRow.sender == member))
            return list((await s.execute(q)).scalars())


def mail_dict(row: MailboxRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "team_id": row.team_id,
        "recipient": row.recipient,
        "sender": row.sender,
        "type": row.type,
        "summary": row.summary,
        "text": row.text,
        "read": row.read,
        "created_at": row.created_at,
    }
