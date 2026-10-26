"""G5a session operations: rename, read-only share links, retry, revert (spec §18).

Like `sessions_ops`, these refuse while a turn is in flight where history changes.
History edits are recorded as events (the log stays append-only): `session.reverted`
carries what was cut, so a revert is auditable and recoverable from the log.
Share tokens are capability links: only their SHA-256 is stored (in a `session.shared`
event); `session.unshared` revokes every link of the session.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from auoreo.core.db import EventRow, MessageRow
from auoreo.core.redaction import redact_obj
from auoreo.core.sessions_ops import _require, session_info

if TYPE_CHECKING:
    from auoreo.core.db import SessionRow
    from auoreo.core.services import Services

MAX_TITLE = 255
REVERT_EVENT_CAP = 256 * 1024
"""Removed messages ride in `session.reverted` while their JSON stays under this size."""
SHARE_PREFIX = "shr_"


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def rename_session(svc: Services, session_id: str, title: str) -> SessionRow:
    title = " ".join(title.split())[:MAX_TITLE]
    if not title:
        raise ValueError("title must not be empty")
    await _require(svc, session_id)
    row = await svc.update_session(session_id, title=title)
    assert row is not None
    await svc.emit(session_id, "session.renamed", {"title": title})
    return row


# ------------------------------------------------------------------ share links
async def create_share(svc: Services, session_id: str) -> str:
    """Mint a read-only share token. Returned once; only its digest is stored."""
    await _require(svc, session_id)
    token = SHARE_PREFIX + secrets.token_urlsafe(24)
    await svc.emit(session_id, "session.shared", {"token_sha256": token_digest(token)})
    await svc.audit.record(
        actor="user", action="session.shared", session_id=session_id, decision="allow", reason="share link"
    )
    return token


async def revoke_shares(svc: Services, session_id: str) -> int:
    """Revoke every share link of the session; returns how many were live."""
    await _require(svc, session_id)
    live = len(await _live_digests(svc, session_id))
    await svc.emit(session_id, "session.unshared", {"revoked": live})
    return live


async def _live_digests(svc: Services, session_id: str) -> set[str]:
    async with svc.db.session() as s:
        q = (
            select(EventRow)
            .where(EventRow.aggregate_id == session_id)
            .where(EventRow.type.in_(("session.shared", "session.unshared")))
            .order_by(EventRow.seq)
        )
        rows = list((await s.execute(q)).scalars())
    live: set[str] = set()
    for r in rows:
        if r.type == "session.unshared":
            live.clear()
        elif digest := r.data.get("token_sha256"):
            live.add(str(digest))
    return live


async def resolve_share(svc: Services, token: str) -> str | None:
    """The session a live share token points at, else None (unknown or revoked)."""
    if not token.startswith(SHARE_PREFIX) or len(token) > 128:
        return None
    digest = token_digest(token)
    async with svc.db.session() as s:
        q = select(EventRow.aggregate_id, EventRow.data).where(EventRow.type == "session.shared")
        hits = [agg for agg, data in (await s.execute(q)).all() if data.get("token_sha256") == digest]
    for session_id in hits:
        if digest in await _live_digests(svc, session_id) and await svc.get_session(session_id):
            return str(session_id)
    return None


async def shared_view(svc: Services, token: str) -> dict[str, Any] | None:
    """Session info + messages with secrets redacted (forced, whatever `redact_secrets` says)."""
    session_id = await resolve_share(svc, token)
    if session_id is None:
        return None
    row = await _require(svc, session_id)
    messages = await svc.coordinator.runner.load_history(session_id)
    return {"session": session_info(row), "messages": redact_obj(messages, force=True)}


# ------------------------------------------------------------------ revert / retry
async def revert_session(svc: Services, session_id: str, to_idx: int) -> int:
    """Keep messages `0..to_idx` (`-1` clears all); later ones are cut. Returns the count cut."""
    await _require(svc, session_id, idle=True)
    if to_idx < -1:
        raise ValueError("message index must be >= -1")
    async with svc.db.session() as s:
        q = (
            select(MessageRow)
            .where(MessageRow.session_id == session_id, MessageRow.idx > to_idx)
            .order_by(MessageRow.idx)
        )
        cut = [dict(r.message) for r in (await s.execute(q)).scalars()]
        if cut:
            await s.execute(
                delete(MessageRow).where(MessageRow.session_id == session_id, MessageRow.idx > to_idx)
            )
            await s.commit()
    if not cut:
        return 0
    svc.coordinator.forget(session_id)  # the next turn reloads the shortened history
    data: dict[str, Any] = {"to_idx": to_idx, "removed": len(cut)}
    if len(json.dumps(cut, default=str)) <= REVERT_EVENT_CAP:
        data["removed_messages"] = cut
    await svc.emit(session_id, "session.reverted", data)
    return len(cut)


def last_user_turn(history: list[dict[str, Any]]) -> tuple[int, str] | None:
    """Index and text of the last plain-text user message (image follow-ups are skipped)."""
    for i in range(len(history) - 1, -1, -1):
        m = history[i]
        if m.get("role") == "user" and isinstance(m.get("content"), str) and m["content"].strip():
            return i, m["content"]
    return None


async def retry_session(svc: Services, session_id: str) -> tuple[str | None, str]:
    """Cut the last user turn (and everything after it) and queue its text again."""
    await _require(svc, session_id, idle=True)
    found = last_user_turn(await svc.coordinator.runner.load_history(session_id))
    if found is None:
        raise ValueError("session has no user message to retry")
    idx, text = found
    await revert_session(svc, session_id, idx - 1)
    await svc.emit(session_id, "session.retried", {"from_idx": idx})
    return await svc.coordinator.submit(session_id, text), text
