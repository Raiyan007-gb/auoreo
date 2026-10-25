"""Proactive suggestion engine (spec §2.3, L687, L889, L1028, L1033).

Consent-first: `propose()` only records a suggestion; nothing runs until a human calls
`accept()`. At most `suggestions_max_pending` are pending (the oldest is expired), a
pending `dedupe_key` is never proposed twice, and expired/denied suggestions are kept as
tombstones that can never be accepted. Unattended sessions (jobs/webhooks) may propose but
never accept. A suggestion with `deliver != "origin"` gets one outbox delivery; on failure
one retry to `home`, then it is tombstoned (`suggestion.failed`). Quiet hours defer the
delivery (the 60 s sweep sends it later), never the proposal.
"""

from __future__ import annotations

import asyncio
import builtins
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update

from auoreo.core.db import DeliveryRow, SuggestionRow, utcnow
from auoreo.suggestions import delivery

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)
SWEEP_EVERY_S = 60.0
TOMBSTONES = frozenset({"denied", "expired", "tombstoned"})
"""Terminal, never-executable states (spec L889: expired/denied suggestions are tombstoned)."""
STATUSES = ("pending", "accepted", *sorted(TOMBSTONES))


class SuggestionError(ValueError):
    """A refused suggestion operation (unknown id, not pending, unattended accept, …)."""


@dataclass(frozen=True)
class Accepted:
    row: SuggestionRow
    session_id: str | None = None
    run_id: str | None = None


def suggestion_event(row: SuggestionRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "title": row.title,
        "status": row.status,
        "deliver": row.deliver,
        "origin_session": row.origin_session,
        "dedupe_key": row.dedupe_key,
    }


class SuggestionEngine:
    def __init__(self, services: Services) -> None:
        self.s = services
        self.local_now: Callable[[], datetime] = datetime.now
        """Local wall clock for quiet hours (tests freeze it)."""
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._sends: set[asyncio.Task[Any]] = set()

    @property
    def max_pending(self) -> int:
        return self.s.settings.suggestions_max_pending

    # ------------------------------------------------------------------ reads
    async def get(self, suggestion_id: int) -> SuggestionRow | None:
        async with self.s.db.session() as s:
            return await s.get(SuggestionRow, suggestion_id)

    async def list(self, status: str | None = None, limit: int = 100) -> list[SuggestionRow]:
        q = select(SuggestionRow).order_by(SuggestionRow.id.desc()).limit(min(max(limit, 1), 1000))
        if status and status != "all":
            if status not in STATUSES:
                raise SuggestionError(f"unknown status '{status}' (known: {', '.join(STATUSES)})")
            q = q.where(SuggestionRow.status == status)
        async with self.s.db.session() as s:
            return list((await s.execute(q)).scalars())

    # ---------------------------------------------------------------- propose
    async def propose(
        self,
        kind: str,
        title: str,
        body: str = "",
        *,
        deliver: str = "origin",
        origin_session: str | None = None,
        action: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
        ttl_s: int = 86400,
    ) -> tuple[SuggestionRow, bool]:
        """Record a pending suggestion → (row, created). A pending row with the same
        `dedupe_key` is returned unchanged with `created=False`."""
        if not self.s.settings.suggestions_enabled:
            raise SuggestionError("suggestions are disabled (settings.suggestions_enabled)")
        if not title.strip():
            raise SuggestionError("title must not be empty")
        try:
            delivery.validate_deliver(self.s, deliver)
        except ValueError as exc:
            raise SuggestionError(str(exc)) from exc
        action = dict(action or {})
        if action.get("type") == "prompt" and not str(action.get("text") or "").strip():
            raise SuggestionError("a prompt action needs non-empty `text`")
        now = utcnow()
        async with self._lock:
            if dedupe_key:
                async with self.s.db.session() as s:
                    q = select(SuggestionRow).where(
                        SuggestionRow.dedupe_key == dedupe_key[:128], SuggestionRow.status == "pending"
                    )
                    existing = (await s.execute(q.limit(1))).scalar()
                if existing is not None:
                    return existing, False
            row = SuggestionRow(
                kind=(kind or "info")[:32],
                title=title.strip()[:300],
                body=(body or "")[:4000],
                status="pending",
                deliver=deliver[:64],
                origin_session=origin_session,
                action=action,
                dedupe_key=dedupe_key[:128] if dedupe_key else None,
                created_at=now,
                updated_at=now,
                expires_at=now + timedelta(seconds=max(int(ttl_s), 1)),
            )
            async with self.s.db.session() as s:
                s.add(row)
                await s.commit()
            evicted = await self._evict_overflow()
        await self.s.emit("server", "suggestion.proposed", suggestion_event(row))
        for old in evicted:
            await self.s.emit(
                "server", "suggestion.expired", suggestion_event(old) | {"reason": "max_pending"}
            )
        if row.deliver != "origin":
            self._spawn(self.deliver(row.id))
        return row, True

    async def _evict_overflow(self) -> builtins.list[SuggestionRow]:
        """Expire the oldest pending rows beyond `max_pending` (caller holds the lock)."""
        async with self.s.db.session() as s:
            q = (
                select(SuggestionRow)
                .where(SuggestionRow.status == "pending")
                .order_by(SuggestionRow.created_at.desc(), SuggestionRow.id.desc())
                .offset(self.max_pending)
            )
            rows = builtins.list((await s.execute(q)).scalars())
            for r in rows:
                r.status = "expired"
                r.updated_at = utcnow()
            await s.commit()
        return rows

    # -------------------------------------------------------- accept / deny
    async def accept(
        self, suggestion_id: int, *, session_id: str | None = None, interactive: bool = True
    ) -> Accepted:
        """Record human consent; a `{type: prompt, agent?, text}` action then submits one
        turn in the origin session (or a new one when it is gone or unattended)."""
        if not interactive or (session_id and await self.is_unattended(session_id)):
            await self.s.audit.record(
                actor="automation",
                action="suggestion.accept",
                session_id=session_id,
                decision="deny",
                reason="unattended sessions may only propose",
                detail={"suggestion_id": suggestion_id},
            )
            raise SuggestionError("unattended sessions may only propose suggestions, never accept them")
        row = await self._transition(suggestion_id, "accepted")
        await self.s.audit.record(
            actor="user",
            action="suggestion.accept",
            session_id=session_id,
            decision="allow",
            reason="explicit consent",
            detail={"suggestion_id": row.id, "action": row.action},
        )
        sid, run_id = await self._run_action(row)
        await self.s.emit(
            "server", "suggestion.accepted", suggestion_event(row) | {"session_id": sid, "run_id": run_id}
        )
        return Accepted(row, sid, run_id)

    async def deny(self, suggestion_id: int) -> SuggestionRow:
        row = await self._transition(suggestion_id, "denied")
        await self.s.audit.record(
            actor="user", action="suggestion.deny", decision="deny", detail={"suggestion_id": row.id}
        )
        await self.s.emit("server", "suggestion.denied", suggestion_event(row))
        return row

    async def _transition(self, suggestion_id: int, status: str) -> SuggestionRow:
        async with self._lock, self.s.db.session() as s:
            row = await s.get(SuggestionRow, suggestion_id)
            if row is None:
                raise SuggestionError(f"suggestion {suggestion_id} not found")
            if row.status == "pending" and row.expires_at is not None and _aware(row.expires_at) <= utcnow():
                row.status = "expired"
                row.updated_at = utcnow()
                await s.commit()
                raise SuggestionError(f"suggestion {suggestion_id} has expired")
            if row.status != "pending":
                raise SuggestionError(f"suggestion {suggestion_id} is {row.status}, not pending")
            row.status = status
            row.updated_at = utcnow()
            await s.commit()
            return row

    async def is_unattended(self, session_id: str) -> bool:
        """A session started by the deliveries worker (job/webhook run) is unattended."""
        async with self.s.db.session() as s:
            q = select(DeliveryRow.id).where(
                DeliveryRow.session_id == session_id, DeliveryRow.kind.in_(("job", "webhook"))
            )
            return (await s.execute(q.limit(1))).first() is not None

    async def _run_action(self, row: SuggestionRow) -> tuple[str | None, str | None]:
        action = row.action or {}
        if action.get("type") != "prompt":
            return None, None
        agent = action.get("agent") or None
        sid = row.origin_session
        ses = await self.s.get_session(sid) if sid else None
        if ses is None or ses.archived or await self.is_unattended(ses.id):
            new = await self.s.create_session(title=f"suggestion: {row.title}"[:120], agent=agent)
            sid = new.id
        assert sid is not None
        run_id = await self.s.coordinator.submit(sid, str(action["text"]), agent=agent)
        return sid, run_id

    # --------------------------------------------------------------- delivery
    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._sends.add(task)
        task.add_done_callback(self._sends.discard)

    async def drain(self, timeout: float = 10.0) -> None:
        """Wait for in-flight deliveries (tests, shutdown)."""
        if self._sends:
            await asyncio.wait(list(self._sends), timeout=timeout)

    def quiet(self) -> bool:
        try:
            return delivery.in_quiet(self.s.settings.suggestions_quiet_hours, self.local_now())
        except ValueError:
            log.warning(
                "ignoring malformed suggestions_quiet_hours %r", self.s.settings.suggestions_quiet_hours
            )
            return False

    async def deliver(self, suggestion_id: int) -> str:
        """Deliver one pending suggestion → `delivered | failed | deferred | skipped`."""
        row = await self.get(suggestion_id)
        if row is None or row.status != "pending" or row.deliver == "origin" or row.delivery_id is not None:
            return "skipped"
        if self.quiet():
            return "deferred"
        specs = await delivery.targets_for(self.s, row.deliver)
        did, ok = await delivery.send_once(self.s, row, specs)
        if did is None:
            return "skipped"  # another process made this attempt
        await self._set_delivery(row.id, did)
        home = self.s.settings.suggestions_home
        if not ok and specs != [home]:
            fid, ok = await delivery.send_once(self.s, row, [home], "-home")
            if fid is not None:
                await self._set_delivery(row.id, fid)
                did = fid
        if ok:
            await self.s.emit("server", "suggestion.delivered", suggestion_event(row) | {"delivery_id": did})
            return "delivered"
        async with self.s.db.session() as s:
            await s.execute(
                update(SuggestionRow)
                .where(SuggestionRow.id == row.id, SuggestionRow.status == "pending")
                .values(status="tombstoned", updated_at=utcnow())
            )
            await s.commit()
        row.status = "tombstoned"
        await self.s.emit("server", "suggestion.failed", suggestion_event(row) | {"delivery_id": did})
        return "failed"

    async def _set_delivery(self, suggestion_id: int, delivery_id: int) -> None:
        async with self.s.db.session() as s:
            await s.execute(
                update(SuggestionRow)
                .where(SuggestionRow.id == suggestion_id)
                .values(delivery_id=delivery_id, updated_at=utcnow())
            )
            await s.commit()

    # ------------------------------------------------------------------ sweep
    async def sweep(self) -> dict[str, int]:
        """Tombstone expired pending suggestions, then send deliveries quiet hours deferred."""
        now = utcnow()
        async with self._lock, self.s.db.session() as s:
            q = select(SuggestionRow).where(
                SuggestionRow.status == "pending", SuggestionRow.expires_at <= now
            )
            expired = builtins.list((await s.execute(q)).scalars())
            for r in expired:
                r.status = "expired"
                r.updated_at = now
            await s.commit()
        for r in expired:
            await self.s.emit("server", "suggestion.expired", suggestion_event(r) | {"reason": "ttl"})
        sent = 0
        if not self.quiet():
            async with self.s.db.session() as s:
                ids = select(SuggestionRow.id).where(
                    SuggestionRow.status == "pending",
                    SuggestionRow.deliver != "origin",
                    SuggestionRow.delivery_id.is_(None),
                )
                due = builtins.list((await s.execute(ids)).scalars())
            for sid in due:
                if await self.deliver(sid) in {"delivered", "failed"}:
                    sent += 1
        return {"expired": len(expired), "delivered": sent}

    def start_loop(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="suggestions-sweep")

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(SWEEP_EVERY_S)
            try:
                await self.sweep()
            except Exception:  # a failed sweep must not kill the loop
                log.exception("suggestion sweep failed")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        await self.drain()


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def suggestions_of(services: Services) -> SuggestionEngine:
    eng = services.ext.get("suggestions")
    return eng if isinstance(eng, SuggestionEngine) else SuggestionEngine(services)


async def start(services: Services) -> SuggestionEngine:
    eng = SuggestionEngine(services)
    if services.settings.suggestions_enabled:
        eng.start_loop()
    return eng
