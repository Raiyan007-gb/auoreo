"""Manual compaction (spec §11.9 S4c) and the soft-archive of compacted messages."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.db import MessageArchiveRow
from auoreo.runtime.compaction import CompactionError
from auoreo.schema.chat import ArchivedMessage, CompactionOut, CompactRequest

router = APIRouter(prefix="/api/sessions", tags=["compaction"], dependencies=AUTH)


@router.post("/{session_id}/compact", response_model=CompactionOut)
async def compact(session_id: str, svc: ServicesDep, body: CompactRequest | None = None) -> CompactionOut:
    if await svc.get_session(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    if svc.coordinator.is_running(session_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "session is running; compact when idle")
    try:
        res = await svc.coordinator.runner.compaction.compact(
            session_id, trigger="manual", retain_ratio=body.retain_ratio if body else None
        )
    except CompactionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.reason) from exc
    return CompactionOut(
        compaction_id=res.compaction_id,
        pre_tokens=res.pre_tokens,
        post_tokens=res.post_tokens,
        archived=res.archived,
        pruned=res.pruned,
        summary_chars=res.summary_chars,
    )


@router.get("/{session_id}/archive", response_model=list[ArchivedMessage])
async def archive(session_id: str, svc: ServicesDep, limit: int = 1000) -> list[ArchivedMessage]:
    """Archived messages, newest compaction first; original order within a compaction."""
    if await svc.get_session(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    async with svc.db.session() as s:
        q = select(MessageArchiveRow).where(MessageArchiveRow.session_id == session_id)
        rows = list((await s.execute(q.order_by(MessageArchiveRow.id))).scalars())
    groups: dict[str, list[MessageArchiveRow]] = {}
    for r in rows:
        groups.setdefault(r.compaction_id, []).append(r)
    ordered = [r for cid in reversed(groups) for r in groups[cid]]
    return [
        ArchivedMessage(
            compaction_id=r.compaction_id,
            idx=r.idx,
            role=str(r.message.get("role", "")),
            content=r.message.get("content") if isinstance(r.message.get("content"), str) else None,
            tool_calls=r.message.get("tool_calls"),
            tool_call_id=r.message.get("tool_call_id"),
            archived_at=r.archived_at,
        )
        for r in ordered[: max(1, min(limit, 10_000))]
    ]
