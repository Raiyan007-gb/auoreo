"""Scheduler (spec §16.7): APScheduler only fires triggers; AUOREO owns the run.

A firing inserts a `job_runs` row (unique `(job, scheduled_for)` → duplicates skipped),
renders the prompt, enqueues a `deliveries` row (kind `job`), claims it and runs it through
the outbox worker (own session, non-interactive, `timeout_s`), then records the outcome.
Pause state persists in `<home>/cron_state.json`; jobs re-sync when `harness.generation`
changes (polled every 30 s) or on `resync()`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from auoreo.automation import lock
from auoreo.automation.deliveries import DeliveryWorker, outbox_of
from auoreo.automation.schedules import Schedule, ScheduleError, missed_fire, parse_schedule
from auoreo.automation.templating import render
from auoreo.core.db import JobRunRow, utcnow
from auoreo.harness.manifests import JobManifest

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)
WATCH_INTERVAL_S = 30.0


class JobScheduler:
    def __init__(self, services: Services) -> None:
        self.s = services
        self.aps: Any = None  # AsyncIOScheduler while active
        self.active = False
        self.errors: dict[str, str] = {}
        self.trigger_overrides: dict[str, Any] = {}
        """Test seam: job name → APScheduler trigger used instead of the parsed schedule."""
        self._schedules: dict[str, Schedule] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._generation = 0
        self.watch_interval_s = WATCH_INTERVAL_S
        self._state_mtime = 0.0

    # ------------------------------------------------------------ state
    @property
    def state_path(self) -> Path:
        return Path(self.s.settings.home) / "cron_state.json"

    def paused(self) -> set[str]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        return {str(n) for n in data.get("paused", [])} if isinstance(data, dict) else set()

    def _save_paused(self, names: set[str]) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"paused": sorted(names)}, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.state_path)

    def manifests(self) -> dict[str, JobManifest]:
        return {n: d.manifest for n, d in self.s.harness.jobs.items()}

    def schedule_of(self, name: str) -> Schedule | None:
        if name in self._schedules:
            return self._schedules[name]
        m = self.manifests().get(name)
        if m is None:
            return None
        try:
            return parse_schedule(m.schedule, m.tz, name)
        except ScheduleError:
            return None

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if not self.s.settings.scheduler_enabled or not lock.acquire(self.s.settings.home, self.s):
            return
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        self.active = True
        await self._recover()
        self.aps = AsyncIOScheduler(timezone=UTC)
        self.aps.start()
        await self.resync()
        self._spawn(self._delayed_catch_up())
        self._spawn(self._watch())

    async def _recover(self) -> None:
        async with self.s.db.session() as s:
            rows = (
                await s.execute(select(JobRunRow).where(JobRunRow.status.in_(("pending", "running"))))
            ).scalars()
            for row in rows:
                row.status, row.error, row.finished_at = "failed", "server stopped during the run", utcnow()
            await s.commit()

    async def resync(self) -> None:
        """Register every enabled job (replace_existing) and drop the ones that went away."""
        self._generation = self.s.harness.generation
        with contextlib.suppress(OSError):
            self._state_mtime = self.state_path.stat().st_mtime
        wanted: dict[str, Schedule] = {}
        self.errors.clear()
        for name, m in self.manifests().items():
            if not m.enabled:
                continue
            try:
                wanted[name] = parse_schedule(m.schedule, m.tz, name)
            except ScheduleError as exc:
                self.errors[name] = str(exc)
                log.error("%s", exc)
        self._schedules = wanted
        if self.aps is None:
            return
        for job in self.aps.get_jobs():
            if job.id not in wanted:
                job.remove()
        paused = self.paused()
        for name, sched in wanted.items():
            if sched.kind == "date" and await self.last_run(name) is not None:
                continue  # one-shot already ran
            self.aps.add_job(
                self._on_trigger,
                self.trigger_overrides.get(name, sched.trigger),
                args=[name],
                id=name,
                name=name,
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=int(sched.grace_s()),
            )
            if name in paused:
                self.aps.pause_job(name)

    async def _watch(self) -> None:
        """Re-sync when the harness generation (reload) or the pause file changed. Polls
        instead of subscribing to the bus so the scheduler never holds a subscriber."""
        while True:
            await asyncio.sleep(self.watch_interval_s)
            if self._generation != self.s.harness.generation or self._state_changed():
                try:
                    await self.resync()
                except Exception:  # keep watching; the next tick retries
                    log.exception("scheduler resync failed")

    def _state_changed(self) -> bool:
        try:
            return self.state_path.stat().st_mtime != self._state_mtime
        except OSError:
            return False

    async def _delayed_catch_up(self) -> None:
        await asyncio.sleep(self.s.settings.automation_start_delay_s)
        await self.catch_up()

    async def catch_up(self, now: datetime | None = None) -> list[str]:
        """Fire once (catch_up=True) for each job whose last fire was missed recently."""
        now = now or datetime.now(UTC)
        fired: list[str] = []
        paused = self.paused()
        for name, sched in list(self._schedules.items()):
            m = self.manifests().get(name)
            if m is None or not m.catch_up or name in paused:
                continue
            last = await self.last_run(name)
            when = missed_fire(sched, last.scheduled_for if last else None, now)
            if when is not None and await self.fire(name, scheduled_for=when, catch_up=True) is not None:
                fired.append(name)
        return fired

    def _spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def close(self) -> None:
        if self.aps is not None:
            self.aps.shutdown(wait=False)
            self.aps = None
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if self.active:
            lock.release(self.s.settings.home, self.s)
            self.active = False

    # ------------------------------------------------------------ runs
    async def _on_trigger(self, name: str) -> None:
        sched = self._schedules.get(name)
        now = datetime.now(UTC)
        when = (
            now.replace(second=0, microsecond=0)
            if sched and sched.kind == "cron"
            else now.replace(microsecond=0)
        )
        # own task so close() can cancel it; awaited so max_instances=1 spans the whole run
        await self._spawn(self.fire(name, scheduled_for=when))

    async def fire(
        self, name: str, *, scheduled_for: datetime | None = None, catch_up: bool = False
    ) -> JobRunRow | None:
        run_id = await self.start_run(name, scheduled_for=scheduled_for, catch_up=catch_up)
        return None if run_id is None else await self.execute(run_id)

    async def start_run(
        self, name: str, *, scheduled_for: datetime | None = None, catch_up: bool = False
    ) -> int | None:
        """Insert the `job_runs` row; None when this (job, scheduled_for) already ran."""
        if name not in self.manifests():
            raise KeyError(f"unknown job '{name}'")
        row = JobRunRow(
            job=name, scheduled_for=scheduled_for or utcnow(), status="pending", catch_up=catch_up
        )
        try:
            async with self.s.db.session() as s:
                s.add(row)
                await s.commit()
        except IntegrityError:
            log.info("job %s already ran for %s; skipped", name, scheduled_for)
            return None
        return row.id

    def run_now(self, run_id: int) -> asyncio.Task[Any]:
        return self._spawn(self.execute(run_id))

    async def execute(self, run_id: int) -> JobRunRow:
        async with self.s.db.session() as s:
            run = await s.get(JobRunRow, run_id)
            if run is None:
                raise KeyError(f"unknown run {run_id}")
            run.status, run.started_at = "running", utcnow()
            await s.commit()
        name = run.job
        await self.s.emit(
            "server",
            "cron.fired",
            {"job": name, "run_id": run_id, "session_id": None, "catch_up": run.catch_up},
        )
        m = self.manifests().get(name)
        session_id: str | None = None
        error: str | None = None
        delivery_id: int | None = None
        if m is None:
            error = "job definition disappeared"
        else:
            row = await self._run_delivery(m, run_id)
            delivery_id = row.id if row else None
            session_id = row.session_id if row else None
            if row is None or row.status != "delivered":
                error = (row.error if row else None) or "delivery did not complete"
        status = "ok" if error is None else "failed"
        async with self.s.db.session() as s:
            run = await s.get(JobRunRow, run_id)
            assert run is not None
            run.status, run.finished_at, run.session_id, run.error = (
                status,
                utcnow(),
                session_id,
                error and error[:2000],
            )
            await s.commit()
        data = {"job": name, "run_id": run_id, "session_id": session_id, "delivery_id": delivery_id}
        await self.s.emit(
            "server",
            "cron.delivered" if error is None else "cron.failed",
            data | ({"error": error} if error else {}),
        )
        return run

    async def _run_delivery(self, m: JobManifest, run_id: int) -> Any:
        sched = self.schedule_of(m.name)
        tz = sched.tz if sched else UTC
        prompt = render(m.prompt, {"now": datetime.now(tz).isoformat(timespec="minutes"), "job": m.name})
        payload = {
            "prompt": prompt,
            "agent": m.agent,
            "workflow": m.workflow,
            "deliver": list(m.deliver),
            "timeout_s": m.timeout_s,
            "job": m.name,
            "run_id": run_id,
        }
        outbox = outbox_of(self.s)
        row = await outbox.enqueue(
            "job", m.name, f"run-{run_id}", payload, m.deliver[0] if m.deliver else "log"
        )
        if row is None:
            return None
        claimed = await outbox.claim(row.id)
        if claimed is None:  # the background worker took it first
            return await outbox.wait_terminal(row.id, m.timeout_s + 60, poll=0.5)
        worker = self.s.ext.get("deliveries")
        if not isinstance(worker, DeliveryWorker):
            worker = DeliveryWorker(self.s)
        return await worker.process(claimed)

    # ------------------------------------------------------------ queries / control
    async def last_run(self, name: str) -> JobRunRow | None:
        rows = await self.runs(name, 1)
        return rows[0] if rows else None

    async def runs(self, name: str, limit: int = 20) -> list[JobRunRow]:
        q = (
            select(JobRunRow)
            .where(JobRunRow.job == name)
            .order_by(JobRunRow.scheduled_for.desc(), JobRunRow.id.desc())
            .limit(min(max(limit, 1), 500))
        )
        async with self.s.db.session() as s:
            return list((await s.execute(q)).scalars())

    def next_run(self, name: str) -> datetime | None:
        if name in self.paused():
            return None
        if self.aps is not None:
            job = self.aps.get_job(name)
            return job.next_run_time if job is not None else None
        m = self.manifests().get(name)
        sched = self.schedule_of(name)
        return sched.next_fire() if m is not None and m.enabled and sched else None

    async def set_paused(self, name: str, paused: bool) -> None:
        if name not in self.manifests():
            raise KeyError(f"unknown job '{name}'")
        names = self.paused()
        names = names | {name} if paused else names - {name}
        self._save_paused(names)
        if self.aps is not None and self.aps.get_job(name) is not None:
            (self.aps.pause_job if paused else self.aps.resume_job)(name)
        with contextlib.suppress(OSError):
            self._state_mtime = self.state_path.stat().st_mtime


async def start(services: Services) -> JobScheduler:
    sched = JobScheduler(services)
    await sched.start()
    return sched


def scheduler_of(services: Services) -> JobScheduler:
    """The running scheduler, or a passive one (CLI / extension disabled) for queries and runs."""
    sched = services.ext.get("scheduler")
    return sched if isinstance(sched, JobScheduler) else JobScheduler(services)
