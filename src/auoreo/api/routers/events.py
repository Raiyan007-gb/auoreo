"""`GET /api/events` — the durable event stream (SSE): replay, then live tail.
`POST /api/events:tail` — the latest seq plus a short tail: SSE ending in `[DONE]`, or JSON."""

import asyncio
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.sse import EventSourceResponse, ServerSentEvent, format_sse_event
from sqlalchemy import select

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core import observability as obs
from auoreo.core.bus import channel_matches, invalid_channels, store_channels
from auoreo.core.db import EventRow
from auoreo.core.event_store import REPLAY_LIMIT
from auoreo.core.services import Services
from auoreo.schema.chat import LatestSeq
from auoreo.schema.events import ServerEvent
from auoreo.schema.events_tail import EventsTail, EventsTailRequest

router = APIRouter(prefix="/api", tags=["events"], dependencies=AUTH)


def parse_channels(session_id: list[str] | None, channels: list[str] | None) -> frozenset[str] | None:
    """`?session_id=` (repeatable) and `?channels=a,b` (repeatable, comma-separated) union."""
    out = {f"session:{s}" for s in session_id or []}
    for raw in channels or []:
        out |= {c.strip() for c in raw.split(",") if c.strip()}
    if bad := invalid_channels(out):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown channel(s): {', '.join(sorted(bad))}")
    return frozenset(out) or None


async def replay_page(
    svc: Services, cursor: int, channels: frozenset[str] | None, limit: int
) -> tuple[list[ServerEvent], int, bool]:
    """One replay page → (events, next cursor, more?). Job channels filter on the payload, so
    they page the unfiltered stream and drop what does not match."""
    filt = store_channels(channels)
    batch = await svc.events.replay(cursor, filt, limit)
    nxt = batch[-1].seq if batch else cursor
    evs = batch if filt == channels else [e for e in batch if channel_matches(e, channels)]
    return evs, nxt, len(batch) == limit


def _aggregate_ids(filt: frozenset[str]) -> list[str]:
    return [c.split(":", 1)[1] for c in filt if c.startswith("session:")] + (
        ["server"] if "server" in filt else []
    )


async def _backlog_exceeds(svc: Services, cursor: int, channels: frozenset[str] | None, cap: int) -> bool:
    filt = store_channels(channels)
    if filt != channels:  # job channels: no cheap count; replay them in full
        return False
    q = select(EventRow.seq).where(EventRow.seq > cursor).order_by(EventRow.seq).offset(cap).limit(1)
    if filt is not None:
        q = q.where(EventRow.aggregate_id.in_(_aggregate_ids(filt)))
    async with svc.db.session() as s:
        return (await s.execute(q)).first() is not None


@dataclass
class ReplayPlan:
    cursor: int
    channels: frozenset[str] | None
    cap: int
    truncated: bool


async def replay_plan(
    response: Response,
    svc: ServicesDep,
    last_event_id: Annotated[str | None, Header()] = None,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    session_id: Annotated[list[str] | None, Query()] = None,
    channels: Annotated[
        list[str] | None, Query(description="`session:<id>`, `job:<id>`, `server` (comma-separated)")
    ] = None,
) -> ReplayPlan:
    """Decided before the stream starts so a truncated replay can answer 206."""
    cursor = int(last_event_id) if last_event_id and last_event_id.isdigit() else after_seq
    chans = parse_channels(session_id, channels)
    cap = svc.settings.http.replay_cap
    truncated = await _backlog_exceeds(svc, cursor, chans, cap)
    if truncated:
        response.status_code = status.HTTP_206_PARTIAL_CONTENT
    return ReplayPlan(cursor, chans, cap, truncated)


@router.get("/events/latest", response_model=LatestSeq)
async def latest(svc: ServicesDep) -> LatestSeq:
    """Highest event seq so far — lets a client tail from 'now' without replaying history."""
    return LatestSeq(seq=await svc.events.last_seq())


async def _tail(svc: Services, body: EventsTailRequest) -> EventsTail:
    """Latest seq + up to `limit` events: after `after_seq`, or else the most recent ones."""
    chans = parse_channels(None, [",".join(body.channels)] if body.channels else None)
    latest_seq = await svc.events.last_seq()
    cursor = body.after_seq
    if cursor is None:
        filt = store_channels(chans)
        q = (
            select(EventRow.seq)
            .order_by(EventRow.seq.desc())
            .limit(body.limit if filt == chans else REPLAY_LIMIT)
        )
        if filt is not None:
            q = q.where(EventRow.aggregate_id.in_(_aggregate_ids(filt)))
        async with svc.db.session() as s:
            seqs = list((await s.execute(q)).scalars())
        cursor = (min(seqs) - 1) if seqs else latest_seq
    events, _, _ = await replay_page(svc, cursor, chans, REPLAY_LIMIT)
    events = events[-body.limit :] if body.after_seq is None else events[: body.limit]
    return EventsTail(seq=latest_seq, events=events)


def wants_json(accept: str | None) -> bool:
    """JSON snapshot only when the client asks for JSON and not for an event stream."""
    accept = (accept or "").lower()
    return "application/json" in accept and "text/event-stream" not in accept


async def _tail_stream(tail: EventsTail) -> AsyncIterator[bytes]:
    yield format_sse_event(comment="connected", retry=3000)
    for ev in tail.events:
        yield format_sse_event(data_str=ev.model_dump_json(), event=ev.type, id=ev.id)
    yield format_sse_event(data_str="[DONE]", event="done", id=str(tail.seq))


@router.post(
    "/events:tail",
    response_model=EventsTail,
    responses={
        200: {
            "description": "SSE (default): the tail as events, then `event: done` / `data: [DONE]`. "
            "`Accept: application/json` returns the JSON snapshot instead.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def events_tail(
    svc: ServicesDep,
    body: EventsTailRequest | None = None,
    accept: Annotated[str | None, Header()] = None,
) -> Any:
    """MCP-style POST-SSE mirror of the GET replay (spec §25.12): the tail (after `after_seq`,
    else the most recent `limit` events) streamed as SSE and closed by a `[DONE]` sentinel;
    `Accept: application/json` (without `text/event-stream`) gets the `EventsTail` snapshot."""
    tail = await _tail(svc, body or EventsTailRequest())
    if wants_json(accept):
        return tail
    return EventSourceResponse(_tail_stream(tail), headers={"Cache-Control": "no-cache"})


@router.get(
    "/events",
    response_class=EventSourceResponse,
    responses={206: {"description": "Replay truncated at `http.replay_cap`; a `cap_warning` event ends it"}},
)
async def events(
    request: Request,
    svc: ServicesDep,
    plan: Annotated[ReplayPlan, Depends(replay_plan)],
    follow: bool = True,
) -> AsyncIterable[ServerSentEvent]:
    """`Last-Event-ID` wins over `?after_seq=`. Filter with repeated `?session_id=` or
    `?channels=` (`session:<id>`, `job:<id>`, `server`). `follow=false` returns the replay
    only. A replay over `http.replay_cap` answers 206 and ends with `cap_warning`: reconnect
    with `Last-Event-ID` to continue."""
    cursor, channels = plan.cursor, plan.channels
    sub = svc.bus.subscribe(channels)  # subscribe first so nothing falls in the gap
    obs.count("active_sse_connections", 1)  # --- W0-D
    sent = 0
    try:
        yield ServerSentEvent(comment="connected", retry=3000)
        while not plan.truncated or sent < plan.cap:
            room = min(REPLAY_LIMIT, plan.cap - sent) if plan.truncated else REPLAY_LIMIT
            page, cursor_next, more = await replay_page(svc, cursor, channels, room)
            for ev in page:
                sent += 1
                yield ServerSentEvent(data=ev.model_dump(mode="json"), event=ev.type, id=ev.id)
            cursor = cursor_next
            if not more:
                break
        if plan.truncated:
            latest_seq = await svc.events.last_seq()
            warn = {"replayed": sent, "cursor": cursor, "latest_seq": latest_seq, "cap": plan.cap}
            yield ServerSentEvent(data=warn, event="cap_warning", id=str(cursor))
            return
        while follow:
            if await request.is_disconnected() or sub.overflowed:
                break
            if getattr(request.app.state, "shutting_down", False):  # --- H5: lifespan teardown
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
        obs.count("active_sse_connections", -1)
        svc.bus.unsubscribe(sub)
