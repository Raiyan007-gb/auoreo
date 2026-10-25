"""`GET /api/events` — the durable event stream (SSE): replay, then live tail."""

import asyncio
from collections.abc import AsyncIterable
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.event_store import REPLAY_LIMIT
from auoreo.schema.chat import LatestSeq

router = APIRouter(prefix="/api", tags=["events"], dependencies=AUTH)


@router.get("/events/latest", response_model=LatestSeq)
async def latest(svc: ServicesDep) -> LatestSeq:
    """Highest event seq so far — lets a client tail from 'now' without replaying history."""
    return LatestSeq(seq=await svc.events.last_seq())


@router.get("/events", response_class=EventSourceResponse)
async def events(
    request: Request,
    svc: ServicesDep,
    last_event_id: Annotated[str | None, Header()] = None,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    session_id: Annotated[list[str] | None, Query()] = None,
    follow: bool = True,
) -> AsyncIterable[ServerSentEvent]:
    """`Last-Event-ID` wins over `?after_seq=`. Filter with repeated `?session_id=`.
    `follow=false` returns the replay only (useful for tests and scripts)."""
    cursor = after_seq
    if last_event_id and last_event_id.isdigit():
        cursor = int(last_event_id)
    channels = frozenset(f"session:{s}" for s in session_id) if session_id else None
    sub = svc.bus.subscribe(channels)  # subscribe first so nothing falls in the gap
    try:
        yield ServerSentEvent(comment="connected", retry=3000)
        while True:
            batch = await svc.events.replay(cursor, channels)
            for ev in batch:
                cursor = ev.seq
                yield ServerSentEvent(data=ev.model_dump(mode="json"), event=ev.type, id=ev.id)
            if len(batch) < REPLAY_LIMIT:
                break
        while follow:
            if await request.is_disconnected() or sub.overflowed:
                break
            try:
                ev = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            if ev.seq <= cursor:
                continue
            cursor = ev.seq
            yield ServerSentEvent(data=ev.model_dump(mode="json"), event=ev.type, id=ev.id)
    finally:
        svc.bus.unsubscribe(sub)
