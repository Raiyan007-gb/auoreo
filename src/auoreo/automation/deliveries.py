"""Deliveries outbox (spec §16.7): claim-at-most-once `pending → delivering → delivered|failed`.

Any process may `enqueue`; only the automation-lock holder runs the worker. A row that is
still `delivering` when the worker starts belonged to a crashed run: it becomes `unknown`
(audited) and is never retried, because the agent may already have acted.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from auoreo.automation import lock
from auoreo.backends import delivery as providers
from auoreo.core.db import DeliveryRow, utcnow
from auoreo.runtime.inbox import RunOptions

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)
_WAKERS: dict[int, asyncio.Event] = {}  # id(services) → its worker's wake event (no bus subscriber)
TERMINAL = frozenset({"delivered", "failed", "unknown"})
OK_RUN = frozenset({"completed", "max_steps"})
MAX_RESULT_TEXT = 4000


class RunError(Exception):
    """The agent run behind a delivery did not produce a usable result."""


class Outbox:
    def __init__(self, services: Services) -> None:
        self.s = services

    async def enqueue(
        self, kind: str, source: str, external_id: str, payload: dict[str, Any], target: str = "log"
    ) -> DeliveryRow | None:
        """Insert a pending row; `None` when (kind, source, external_id) already exists."""
        row = DeliveryRow(
            kind=kind,
            source=source,
            external_id=external_id[:128],
            payload=payload,
            result={},
            target=target[:64],
            status="pending",
        )
        try:
            async with self.s.db.session() as s:
                s.add(row)
                await s.commit()
        except IntegrityError:
            return None
        if (wake := _WAKERS.get(id(self.s))) is not None:
            wake.set()
        await self.s.emit(
            "server",
            "delivery.queued",
            {"delivery_id": row.id, "kind": kind, "source": source, "external_id": row.external_id},
        )
        return row

    async def exists(self, kind: str, source: str, external_id: str) -> bool:
        async with self.s.db.session() as s:
            q = select(DeliveryRow.id).where(
                DeliveryRow.kind == kind,
                DeliveryRow.source == source,
                DeliveryRow.external_id == external_id[:128],
            )
            return (await s.execute(q)).first() is not None

    async def claim(self, delivery_id: int | None = None) -> DeliveryRow | None:
        """Atomically move one pending row (the oldest, or `delivery_id`) to `delivering`.
        The `status='pending'` guard makes a lost race a no-op (rowcount 0)."""
        now = utcnow()
        target_id: Any = delivery_id
        if target_id is None:
            target_id = (
                select(DeliveryRow.id)
                .where(DeliveryRow.status == "pending")
                .order_by(DeliveryRow.id)
                .limit(1)
                .scalar_subquery()
            )
        stmt = (
            update(DeliveryRow)
            .where(DeliveryRow.id == target_id, DeliveryRow.status == "pending")
            .values(status="delivering", claimed_at=now, updated_at=now, attempts=DeliveryRow.attempts + 1)
            .returning(DeliveryRow.id)
            .execution_options(synchronize_session=False)
        )
        async with self.s.db.session() as s:
            won = (await s.execute(stmt)).scalars().all()
            await s.commit()
        if len(won) != 1:
            return None
        return await self.get(won[0])

    async def claim_next(self) -> DeliveryRow | None:
        return await self.claim(None)

    async def finish(
        self,
        delivery_id: int,
        ok: bool,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        session_id: str | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "status": "delivered" if ok else "failed",
            "result": result or {},
            "error": (error or None) and str(error)[:2000],
            "updated_at": utcnow(),
        }
        if session_id is not None:
            values["session_id"] = session_id
        async with self.s.db.session() as s:
            await s.execute(update(DeliveryRow).where(DeliveryRow.id == delivery_id).values(**values))
            await s.commit()

    async def set_session(self, delivery_id: int, session_id: str) -> None:
        async with self.s.db.session() as s:
            await s.execute(
                update(DeliveryRow).where(DeliveryRow.id == delivery_id).values(session_id=session_id)
            )
            await s.commit()

    async def recover(self) -> int:
        """`delivering` rows left by a crash → `unknown` (never retried)."""
        async with self.s.db.session() as s:
            rows = list(
                (await s.execute(select(DeliveryRow).where(DeliveryRow.status == "delivering"))).scalars()
            )
            for row in rows:
                row.status = "unknown"
                row.error = "server stopped while delivering; not retried"
                row.updated_at = utcnow()
            await s.commit()
        for row in rows:
            await self.s.audit.record(
                actor="automation",
                action="delivery.unknown",
                session_id=row.session_id,
                decision="unknown",
                reason="crash after claim",
                detail={"delivery_id": row.id, "kind": row.kind, "source": row.source},
            )
        return len(rows)

    async def get(self, delivery_id: int) -> DeliveryRow | None:
        async with self.s.db.session() as s:
            return await s.get(DeliveryRow, delivery_id)

    async def list(
        self, status: str | None = None, kind: str | None = None, limit: int = 50
    ) -> list[DeliveryRow]:
        q = select(DeliveryRow).order_by(DeliveryRow.id.desc()).limit(min(max(limit, 1), 1000))
        if status:
            q = q.where(DeliveryRow.status == status)
        if kind:
            q = q.where(DeliveryRow.kind == kind)
        async with self.s.db.session() as s:
            return list((await s.execute(q)).scalars())

    async def wait_terminal(self, delivery_id: int, timeout: float, poll: float = 0.1) -> DeliveryRow | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            row = await self.get(delivery_id)
            if row is None or row.status in TERMINAL or loop.time() >= deadline:
                return row
            await asyncio.sleep(poll)


class DeliveryWorker:
    """Drains the outbox: run the agent in its own non-interactive session, then send the
    final text through the delivery providers in order until one succeeds."""

    def __init__(self, services: Services) -> None:
        self.s = services
        self.outbox = Outbox(services)
        self.active = False
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        if not lock.acquire(self.s.settings.home, self.s):
            return
        self.active = True
        n = await self.outbox.recover()
        if n:
            log.warning("marked %d interrupted deliveries as unknown", n)
        _WAKERS[id(self.s)] = self._wake
        for i in range(self.s.settings.automation_workers):
            self._tasks.append(asyncio.create_task(self._loop(), name=f"deliveries:worker{i}"))

    def wake(self) -> None:
        self._wake.set()

    async def _loop(self) -> None:
        await asyncio.sleep(self.s.settings.automation_start_delay_s)
        while True:
            try:
                while await self.run_once():
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:  # keep draining; one bad row must not stop the worker
                log.exception("delivery worker iteration failed")
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self.s.settings.automation_poll_s)

    async def run_once(self) -> bool:
        row = await self.outbox.claim_next()
        if row is None:
            return False
        await self.process(row)
        return True

    async def process(self, row: DeliveryRow) -> DeliveryRow:
        """Run + deliver one claimed row; always ends in `delivered` or `failed`."""
        meta = {"kind": row.kind, "source": row.source, "external_id": row.external_id, "delivery_id": row.id}
        sid: str | None = None
        result: dict[str, Any] = {}
        error: str | None = None
        try:
            sid, text = await self._run_agent(row)
            result["text"] = text[:MAX_RESULT_TEXT]
            provider, attempts = await self._deliver(row, text, meta | {"session_id": sid})
            result |= {"provider": provider, "attempts": attempts}
            if provider is None:
                error = "; ".join(f"{a['provider']}: {a['detail']}" for a in attempts) or "no delivery target"
        except RunError as exc:
            error = str(exc)
        except Exception as exc:  # never leave a claimed row in `delivering`
            log.exception("delivery %s crashed", row.id)
            error = f"internal error: {exc.__class__.__name__}"
        await self.outbox.finish(row.id, error is None, result, error, sid)
        status = "delivered" if error is None else "failed"
        data = meta | {"session_id": sid, "provider": result.get("provider")}
        await self.s.emit("server", f"delivery.{status}", data | ({"error": error} if error else {}))
        return await self.outbox.get(row.id) or row

    async def _run_agent(self, row: DeliveryRow) -> tuple[str, str]:
        payload = row.payload
        prompt = str(payload.get("prompt") or "")
        timeout = float(payload.get("timeout_s") or self.s.settings.automation_run_timeout_s)
        try:
            ses = await self.s.create_session(
                title=f"{row.kind}:{row.source}"[:120],
                agent=payload.get("agent"),
                workflow=payload.get("workflow"),
            )
        except KeyError as exc:
            raise RunError(str(exc).strip("'\"")) from exc
        await self.outbox.set_session(row.id, ses.id)
        coord = self.s.coordinator
        run_id = await coord.submit(
            ses.id, prompt, interactive=False, options=unattended_options(self.s, row.kind, row.source)
        )
        try:
            await coord.wait(ses.id, timeout)
        except TimeoutError as exc:
            await coord.cancel(ses.id)
            raise RunError(f"agent timed out after {timeout:.0f}s") from exc
        res = coord.result(run_id) if run_id else None
        coord.forget(ses.id)
        if res is None or res.status not in OK_RUN:
            detail = "no result" if res is None else (res.error or {}).get("message") or res.status
            raise RunError(f"agent run failed: {detail}")
        return ses.id, res.text

    async def _deliver(
        self, row: DeliveryRow, text: str, meta: dict[str, Any]
    ) -> tuple[str | None, list[dict[str, Any]]]:
        specs = list(row.payload.get("deliver") or [row.target])
        attempts: list[dict[str, Any]] = []
        for spec in specs:
            name, target = providers.split_spec(str(spec))
            target = target or str(row.payload.get("delivery_target") or "")
            try:
                body, extra = await voice_notes(self.s, name, text)  # --- I7: MEDIA: envelope
                receipt = await providers.resolve(self.s, name).send(target, body, meta=meta | extra)
            except Exception as exc:  # a broken provider counts as a failed attempt
                receipt = providers.DeliveryReceipt(False, f"{exc.__class__.__name__}: {exc}"[:500])
            attempts.append(
                {
                    "provider": name,
                    "ok": receipt.ok,
                    "detail": receipt.detail,
                    "external_id": receipt.external_id,
                }
            )
            if receipt.ok:
                return name, attempts
        return None, attempts

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        if _WAKERS.get(id(self.s)) is self._wake:
            del _WAKERS[id(self.s)]
        if self.active:
            lock.release(self.s.settings.home, self.s)
            self.active = False
        providers.forget(self.s)


async def start(services: Services) -> DeliveryWorker:
    worker = DeliveryWorker(services)
    if services.settings.webhooks_enabled or services.settings.scheduler_enabled:
        await worker.start()
    return worker


def outbox_of(services: Services) -> Outbox:
    worker = services.ext.get("deliveries")
    return worker.outbox if isinstance(worker, DeliveryWorker) else Outbox(services)


async def voice_notes(services: Any, provider: str, text: str) -> tuple[str, dict[str, Any]]:
    """I7 (spec §13.4): `MEDIA:` lines → `meta["media"]` (Opus/MP3 per provider, 16 MiB cap);
    a note that cannot be encoded is reported in the text instead of silently dropped."""
    from auoreo.voice import notes

    body, media, errors = await notes.attach(text, provider, services.settings.home)
    if not media and not errors:
        return body, {}
    return notes.with_errors(body, errors), {"media": media}


UNATTENDED_DENY: tuple[str, ...] = ("memory", "delegate_task", "plugin_*", "cronjob")


def unattended_options(services: Any, kind: str, source: str) -> RunOptions:
    """Webhook/job sessions run headless: no memory writes or recall, no delegation, no
    creator tools, and the manifest's `max_steps` when set (spec §25.11 cron injection defense)."""
    lookup: dict[str, dict[str, Any]] = {"webhook": services.harness.webhooks, "job": services.harness.jobs}
    entry = lookup.get(kind, {}).get(source)
    max_steps = getattr(entry.manifest, "max_steps", None) if entry is not None else None
    return RunOptions(max_steps=max_steps, tools_deny=UNATTENDED_DENY, skip_memory=True)
