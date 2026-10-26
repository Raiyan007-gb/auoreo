"""Kanban dispatcher (spec §16.6, §25.11): picks ready tasks and spawns one worker per task.

Started through `core.extensions.STARTERS` (`auoreo.teams.dispatcher:start` →
`services.ext["dispatcher"]`); it does nothing unless `i3.kanban.dispatch` is on, and only
the holder of the automation single-writer lock dispatches. Every `interval_s`:

1. heartbeat the tasks of running workers (no stale reclaim mid-run);
2. for each ready task on `i3.kanban.boards`, oldest first: skip it while a respawn guard
   holds (1 h after a successful run, 5 min after a quota/rate-limit failure, 24 h after
   any run of a PR task) or while its profile is at its cap (`profile_caps`/`default_cap`);
   otherwise atomically claim it for a new worker name and run a worker session
   (`agent = task.profile or i3.kanban.agent`, non-interactive, no memory) pinned to the
   task's board (`KanbanStore.worker_boards`, see `teams.kanban_guard`).
3. when the run ends: a task still claimed by that worker is failure-counted (`block`, so
   `FAILURE_LIMIT=2` auto-blocks it); the guard is set from the outcome.

Each worker logs to `<home>/kanban/logs/<board>/<worker>.log`, rotated at 2 MiB.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from auoreo.automation import lock
from auoreo.runtime.inbox import RunOptions
from auoreo.teams.kanban import KanbanError, KanbanStore
from auoreo.teams.worker_log import RotatingLog, worker_log_path

if TYPE_CHECKING:
    from auoreo.core.db import KanbanTaskRow
    from auoreo.core.services import Services

log = logging.getLogger(__name__)
QUOTA_CODES = frozenset({"RATE_LIMIT", "QUOTA", "BILLING", "BUDGET"})
PR_RE = re.compile(r"/pull/\d+|^\s*PR\b|pull request", re.IGNORECASE)
WORKER_DENY = ("kanban_list", "kanban_unblock", "delegate_task", "cronjob", "memory")
PROMPT = (
    "You are kanban worker {worker} on board '{board}'. Your task is #{id}: {title}\n\n{body}\n\n"
    "Do the work, then call kanban_complete(task_id={id}, summary=...) with a one-line result; "
    "if you cannot finish, call kanban_block(task_id={id}, reason=...). Heartbeat long work with "
    "kanban_heartbeat. You may only touch board '{board}'."
)


@dataclass
class Worker:
    name: str
    session_id: str
    board: str
    task_id: int
    profile: str
    log: RotatingLog
    is_pr: bool = False
    run_id: str | None = None
    started: float = field(default_factory=time.monotonic)
    task: asyncio.Task[None] | None = None


class KanbanDispatcher:
    def __init__(self, services: Services) -> None:
        self.s = services
        self.running: dict[str, Worker] = {}  # by worker session id
        self.guards: dict[int, tuple[float, str]] = {}  # task id → (not before, why)
        self.active = False
        self._loop_task: asyncio.Task[None] | None = None

    @property
    def cfg(self) -> Any:
        return self.s.settings.i3.kanban

    @property
    def kanban(self) -> KanbanStore:
        teams = self.s.ext.get("teams")
        if teams is None:
            raise RuntimeError("the teams extension is not running")
        return teams.kanban  # type: ignore[no-any-return]

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if not self.cfg.dispatch or not lock.acquire(self.s.settings.home, self.s):
            return
        self.active = True
        self._loop_task = asyncio.create_task(self._loop(), name="kanban:dispatch")

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.interval_s)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # keep dispatching; one bad tick must not stop the loop
                log.exception("kanban dispatch tick failed")

    async def close(self) -> None:
        tasks = [t for t in (self._loop_task, *(w.task for w in self.running.values())) if t]
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        if self.active:
            lock.release(self.s.settings.home, self.s)
            self.active = False

    # ------------------------------------------------------------ dispatch
    def cap(self, profile: str) -> int:
        return int(self.cfg.profile_caps.get(profile, self.cfg.default_cap))

    def guarded(self, task_id: int, now: float | None = None) -> str | None:
        until, why = self.guards.get(task_id, (0.0, ""))
        return why if (now or time.time()) < until else None

    def _guard(self, task_id: int, seconds: float, why: str) -> None:
        until = time.time() + seconds
        if until > self.guards.get(task_id, (0.0, ""))[0]:
            self.guards[task_id] = (until, why)

    async def tick(self) -> list[int]:
        """One dispatch pass; returns the ids of the tasks a worker was spawned for."""
        kb = self.kanban
        for w in list(self.running.values()):
            with contextlib.suppress(KanbanError):
                await kb.heartbeat(w.task_id)
        spawned: list[int] = []
        for board in self.cfg.boards:
            for row in await kb.list_tasks(board, "ready"):
                profile = row.profile or self.cfg.agent
                if self.guarded(row.id) or self._busy(profile) >= self.cap(profile):
                    continue
                if await self._spawn(kb, row, profile):
                    spawned.append(row.id)
        return spawned

    def _busy(self, profile: str) -> int:
        return sum(1 for w in self.running.values() if w.profile == profile)

    async def _is_pr(self, kb: KanbanStore, row: KanbanTaskRow) -> bool:
        if PR_RE.search(row.title):
            return True
        refs = [r.text for r in await kb.comments(row.id, structured=True)]
        return any(PR_RE.search(ref) for ref in refs)

    async def _spawn(self, kb: KanbanStore, row: KanbanTaskRow, profile: str) -> bool:
        if profile not in self.s.harness.agents:
            log.warning("kanban task %s names unknown profile %s; skipped", row.id, profile)
            self._guard(row.id, self.cfg.quota_cooldown_s, "unknown profile")
            return False
        name = f"kw-{secrets.token_hex(4)}"
        claimed = await kb.claim(row.board, name, task_id=row.id)
        if claimed is None:  # someone else took it
            return False
        ses = await self.s.create_session(title=f"kanban:{row.board}#{row.id}"[:120], agent=profile)
        await kb.set_session(row.id, ses.id)
        kb.worker_boards[ses.id] = row.board
        kb.worker_names[ses.id] = name
        path = worker_log_path(self.s.settings.home, row.board, name)
        w = Worker(name, ses.id, row.board, row.id, profile, RotatingLog(path, self.cfg.log_max_bytes))
        w.is_pr = await self._is_pr(kb, claimed)
        w.log.write({"type": "worker.spawned", "task": row.id, "session_id": ses.id, "profile": profile})
        self.running[ses.id] = w
        prompt = PROMPT.format(worker=name, board=row.board, id=row.id, title=row.title, body=row.body)
        options = RunOptions(skip_memory=True, tools_deny=WORKER_DENY)
        w.run_id = await self.s.coordinator.submit(ses.id, prompt, interactive=False, options=options)
        await self.s.emit(
            "server",
            "kanban.worker_spawned",
            {"id": row.id, "board": row.board, "worker": name, "session_id": ses.id, "profile": profile},
        )
        w.task = asyncio.create_task(self._watch(w), name=f"kanban:worker:{name}")
        return True

    async def _watch(self, w: Worker) -> None:
        coord = self.s.coordinator
        status, code = "failed", "TIMEOUT"
        try:
            await coord.wait(w.session_id, self.cfg.run_timeout_s)
            status, code = "completed", ""
        except TimeoutError:
            await coord.cancel(w.session_id)
        finally:
            await self._finish(w, status, code)

    async def _finish(self, w: Worker, status: str, code: str) -> None:
        kb = self.kanban
        coord = self.s.coordinator
        res = coord.result(w.run_id) if w.run_id else None
        if res is not None and res.status not in {"completed", "max_steps"}:
            status, code = "failed", str((res.error or {}).get("code") or res.status)
        events = await self.s.events.replay(0, frozenset({f"session:{w.session_id}"}), 2000)
        w.log.write(
            *({"type": e.type, "seq": e.seq, "data": e.data} for e in events if ".delta" not in e.type)
        )
        row = await kb.get(w.task_id)
        if row.status == "claimed" and row.worker == w.name:
            reason = f"worker {w.name} ended without completing ({code or status})"
            with contextlib.suppress(KanbanError):
                row = await kb.block(w.task_id, reason, w.name)
        if code in QUOTA_CODES:
            self._guard(w.task_id, self.cfg.quota_cooldown_s, "quota cooldown")
        elif row.status == "done":
            self._guard(w.task_id, self.cfg.success_cooldown_s, "recent success")
        if w.is_pr:
            self._guard(w.task_id, self.cfg.pr_cooldown_s, "PR task")
        w.log.write({"type": "worker.finished", "task": w.task_id, "status": row.status, "code": code})
        self.running.pop(w.session_id, None)
        coord.forget(w.session_id)
        kb.worker_boards.pop(w.session_id, None)
        kb.worker_names.pop(w.session_id, None)
        await self.s.emit(
            "server",
            "kanban.worker_finished",
            {"id": w.task_id, "board": w.board, "worker": w.name, "status": row.status, "code": code},
        )


async def start(services: Services) -> KanbanDispatcher:
    disp = KanbanDispatcher(services)
    await disp.start()
    return disp
