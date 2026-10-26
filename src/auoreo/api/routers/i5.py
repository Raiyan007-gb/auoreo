"""I5 endpoints: `POST /api/lsp/stop` (in-process language servers, behind `auoreo lsp stop`)
and `GET /api/memory/usage` (the Memory page's usage + compression gauge)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.db import EventRow
from auoreo.core.reload_kinds import stop_lsp
from auoreo.schema.i5 import LspStopped, LspStopRequest, MemoryUsage

router = APIRouter(dependencies=AUTH)
COMPACTIONS_SCANNED = 5000
"""Most recent `compaction.completed` events summed by the gauge (bounded work per request)."""


@router.post("/api/lsp/stop", response_model=LspStopped, tags=["lsp"])
async def lsp_stop(svc: ServicesDep, body: LspStopRequest | None = None) -> LspStopped:
    """Stop language servers spawned by the `lsp` tool inside this server process (all, or one
    session's / one language's). They start again on the next `lsp` call."""
    from auoreo.tools.lsp_client import manager_for

    req = body or LspStopRequest()
    stopped = await stop_lsp(manager_for(svc), req.session_id, req.language)
    if stopped:
        await svc.emit("server", "lsp.stopped", {"servers": stopped})
    return LspStopped(stopped=stopped, count=len(stopped))


async def _compaction_totals(svc: Any) -> tuple[int, int, int, int]:
    async with svc.db.session() as s:
        rows = (
            await s.execute(
                select(EventRow.data)
                .where(EventRow.type == "compaction.completed")
                .order_by(EventRow.seq.desc())
                .limit(COMPACTIONS_SCANNED)
            )
        ).scalars()
        n = pre = post = archived = 0
        for data in rows:
            n += 1
            pre += int(data.get("pre_tokens") or 0)
            post += int(data.get("post_tokens") or 0)
            archived += int(data.get("archived") or 0)
    return n, pre, post, archived


@router.get("/api/memory/usage", response_model=MemoryUsage, tags=["memory"])
async def memory_usage(svc: ServicesDep) -> MemoryUsage:
    """Fact/chunk/transcript counts, the memory.db size and the compaction savings so far."""
    n, pre, post, archived = await _compaction_totals(svc)
    saved = max(0.0, min(1.0, 1 - post / pre)) if pre > 0 else 0.0
    out = MemoryUsage(
        enabled=svc.memory is not None,
        compactions=n,
        pre_tokens=pre,
        post_tokens=post,
        saved_ratio=round(saved, 4),
        archived_messages=archived,
    )
    db = svc.settings.home / "memory.db"
    out.db_bytes = db.stat().st_size if db.is_file() else 0
    if svc.memory is not None:
        stats: dict[str, Any] = dict(await svc.memory.stats())
        out.facts, out.chunks = int(stats.get("facts", 0)), int(stats.get("chunks", 0))
        out.transcript, out.archived_facts = int(stats.get("transcript", 0)), int(stats.get("archived", 0))
        out.vector = bool(stats.get("vector"))
    return out
