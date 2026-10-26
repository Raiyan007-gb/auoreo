"""Background re-embed job (spec §11.1 L554, §24.3): `memory.embed_model` changed.

Rows whose stored `embed_model` differs from the current one are invisible to the vector
arm (KNN filters by the current model) until this job rewrites their vectors. The job
walks facts then chunks in id order, `batch` rows at a time (default 128), throttled, and
persists `{embed_model, kind, cursor_id, done, total, state}` in memory.db
(`reembed_state`) after every batch, so a restart resumes where it stopped. Progress is
emitted as `memory.reembed.progress` / `memory.reembed.completed`.

Starter `auoreo.memory.reembed:start` starts it when `i4.memory.reembed_auto` and stale rows
exist; `auoreo memory reembed` and `POST /api/memory/reembed` run it on demand.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from typing import TYPE_CHECKING, Any

from auoreo.schema.i4 import ReembedStatus

if TYPE_CHECKING:
    from auoreo.core.services import Services
    from auoreo.core.settings import Settings

log = logging.getLogger(__name__)
ORDER = ("fact", "chunk")


class ReembedJob:
    def __init__(self, services: Services) -> None:
        self.s = services
        self.task: asyncio.Task[ReembedStatus] | None = None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    async def status(self) -> ReembedStatus:
        mem = self.s.memory
        model = self.s.settings.memory.embed_model
        if mem is None:
            return ReembedStatus(embed_model=model, state="disabled")
        st = await mem._run(mem.store.reembed_state) or {}
        stale = int(await mem._run(mem.store.stale_count, model))
        state = "running" if self.running else str(st.get("state") or "idle")
        if st.get("embed_model") != model:
            st = {}
            state = "running" if self.running else ("pending" if stale else "idle")
        return ReembedStatus(
            embed_model=model,
            state=state,
            done=int(st.get("done") or 0),
            total=int(st.get("total") or stale),
            stale=stale,
            error=st.get("error"),
        )

    def start(self) -> bool:
        if self.running or self.s.memory is None:
            return False
        self.task = asyncio.create_task(self.run(), name="memory-reembed")
        return True

    async def run_or_join(self) -> ReembedStatus:
        if self.running and self.task is not None:
            return await self.task
        return await self.run()

    async def run(self, batch: int | None = None, throttle_s: float | None = None) -> ReembedStatus:
        mem = self.s.memory
        if mem is None:
            return ReembedStatus(embed_model=self.s.settings.memory.embed_model, state="disabled")
        cfg = self.s.settings.i4.memory
        batch = batch or cfg.reembed_batch
        throttle_s = cfg.reembed_throttle_s if throttle_s is None else throttle_s
        model = mem.cfg.embed_model
        store = mem.store
        saved: dict[str, Any] | None = await mem._run(store.reembed_state)
        st: dict[str, Any] = saved or {}
        if st.get("embed_model") != model or st.get("state") == "done":
            stale = await mem._run(store.stale_count, model)
            st = {
                "embed_model": model,
                "kind": "fact",
                "cursor_id": 0,
                "done": 0,
                "total": stale,
                "error": None,
            }
        if not store.vec_enabled:
            await mem._run(
                store.save_reembed_state, **{**st, "state": "skipped", "error": "sqlite-vec unavailable"}
            )
            return await self.status()
        await mem._run(store.save_reembed_state, **{**st, "state": "running", "error": None})
        done, start_kind = int(st["done"]), str(st["kind"])
        for kind in ORDER[ORDER.index(start_kind) if start_kind in ORDER else 0 :]:
            cursor = int(st["cursor_id"]) if kind == start_kind else 0
            while rows := await mem._run(store.stale_batch, kind, model, cursor, batch):
                embs = await mem._embed([text for _, text in rows])
                if embs is None or len(embs) != len(rows):
                    err = "embedding call failed; resume with `auoreo memory reembed`"
                    await mem._run(
                        store.save_reembed_state, kind=kind, cursor_id=cursor, state="failed", error=err
                    )
                    await self.s.emit("server", "memory.reembed.failed", {"embed_model": model, "error": err})
                    return await self.status()
                await mem._run(
                    store.set_embeddings, kind, model, [(i, e) for (i, _), e in zip(rows, embs, strict=True)]
                )
                cursor, done = rows[-1][0], done + len(rows)
                await mem._run(store.save_reembed_state, kind=kind, cursor_id=cursor, done=done)
                progress = {"embed_model": model, "kind": kind, "done": done, "total": st["total"]}
                await self.s.emit("server", "memory.reembed.progress", progress)
                if throttle_s:
                    await asyncio.sleep(throttle_s)
        await mem._run(store.save_reembed_state, state="done", done=done)
        await self.s.emit("server", "memory.reembed.completed", {"embed_model": model, "done": done})
        return await self.status()

    async def close(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task


def status_offline(st: Settings) -> ReembedStatus | None:
    """Doctor view without starting services (read-only; None when unknown/encrypted)."""
    path = st.home / "memory.db"
    if not path.is_file():
        return None
    model = st.memory.embed_model
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            stale = sum(
                int(
                    db.execute(
                        f"SELECT count(*) FROM {t} WHERE embed_model IS NOT NULL AND embed_model != ?",
                        (model,),
                    ).fetchone()[0]
                )
                for t in ("facts", "chunks")
            )
            row = db.execute("SELECT embed_model, state, done, total FROM reembed_state").fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return None
    if row is None or row[0] != model:
        return ReembedStatus(
            embed_model=model, state="pending" if stale else "idle", stale=stale, total=stale
        )
    return ReembedStatus(
        embed_model=model, state=str(row[1]), done=int(row[2]), total=int(row[3]), stale=stale
    )


async def start(services: Services) -> ReembedJob | None:
    if services.memory is None:
        return None
    job = ReembedJob(services)
    if services.settings.i4.memory.reembed_auto:
        status = await job.status()
        if status.stale:
            log.info("embed_model changed: re-embedding %d rows in the background", status.stale)
            job.start()
    return job


__all__ = ["ReembedJob", "start", "status_offline"]
