---
name: sse-events
description: Implement the durable event stream — one SSE endpoint, per-client fan-out, Last-Event-ID replay, WebSocket. Use when building agent_core/event_service and agent_api/event.py in this repo.
---

# Durable events + SSE fan-out (plan.md Part C.3, G, Part F `GET /api/event`)

The whole platform streams via **one SSE endpoint**; TUI, web, and SDK are subscribers. Events are durable (SQLite) and replayable by per-aggregate `seq`.

## Event envelope

```python
# agent_schema/event_manifest.py — define every type string here first
# envelope: {"id", "type", "seq", "aggregate_id", "data", "created_at"}
```

Types: `server.connected`, `session.started/updated/ended`, `message.partial/completed`,
`message.part.updated`, `tool.started/tool.completed/tool.error`, `permission.requested`,
`question.asked`, `run.started/run.finished/run.failed`, `model.switched/agent.switched`,
`error`, `config.changed`, `log.*`.

## Subscriber set (per-client queue fan-out)

A single shared queue only delivers to one consumer. Maintain a **set of per-client queues**:

```python
import asyncio

_subscribers: set[asyncio.Queue[EventPayload]] = set()
_lock = asyncio.Lock()

async def publish(payload: EventPayload) -> None:
    async with _lock:
        for q in list(_subscribers):
            await q.put(payload)        # best-effort; bounded queue sizes

async def subscribe() -> asyncio.Queue[EventPayload]:
    q: asyncio.Queue[EventPayload] = asyncio.Queue(maxsize=100)   # bound to prevent memory growth
    async with _lock:
        _subscribers.add(q)
    return q

async def unsubscribe(q) -> None:
    async with _lock:
        _subscribers.discard(q)
```

## SSE endpoint (FastAPI native `fastapi.sse` — recommended)

**FastAPI 0.135+ ships native SSE**: `from fastapi.sse import EventSourceResponse, ServerSentEvent`
(auto `Cache-Control: no-cache`, `X-Accel-Buffering: no`, 15 s keep-alive ping). Use it as the
default. On an older FastAPI, `sse-starlette` provides the identical API (same class/field names) —
switch only the import.

```python
from fastapi.sse import EventSourceResponse, ServerSentEvent   # native (FastAPI >= 0.135)
# from sse_starlette.sse import EventSourceResponse, ServerSentEvent  # fallback (older)
from fastapi import Header, Request

@app.get("/api/event", response_class=EventSourceResponse)
async def event_stream(request: Request, last_event_id: int | None = Header(default=None)):
    # 1) Replay missed durable events from seq after last_event_id (DB query, ordered by seq)
    # 2) Then live-tail the subscriber queue
    q = await subscribe()                      # subscribe AFTER validating auth
    try:
        async for payload in replay_from(last_event_id):         # durable catch-up
            if await request.is_disconnected(): break
            yield ServerSentEvent(data=payload.data, event=payload.type, id=str(payload.seq), retry=3000)
        while True:
            if await request.is_disconnected():
                break
            payload = await q.get()
            yield ServerSentEvent(data=payload.data, event=payload.type, id=str(payload.seq), retry=3000)
    finally:
        await unsubscribe(q)                   # always clean up, incl. on cancel
```

## Production checklist (verified via Exa, 2026)

- Headers: `Cache-Control: no-cache` + `X-Accel-Buffering: no` (sse-starlette sets both automatically).
- Keep-alive: send a `: ping` comment every 15–30 s (sse-starlette `ping` param) to outlast proxy idle timeouts (ALB/Nginx close idle connections at 55–60 s).
- Monotonic `seq` from a **DB sequence / Redis INCR** — never `time.time()` (must be strictly ordered across restarts, plan.md Part G).
- Disconnect handling: check `request.is_disconnected()` before blocking `queue.get()`; re-raise `asyncio.CancelledError` after `finally` cleanup; don't swallow it.
- Proxy: `proxy_buffering off`, `proxy_read_timeout 3600s`, gzip disabled on `text/event-stream`.
- Scale-out (later): swap in-memory set for Redis pub/sub so every worker forwards to its connections (plan.md C.1 addendum); keep the same endpoint/fan-out contract.

## WebSocket (bidirectional)

`/ws` carries client→server (approval answers, edits, cancel) and server→client mirror. Use one `WebSocket` handler (FastAPI `@app.websocket("/ws")`), reusing the same durable event envelope for server→client so replay still works for SSE while WS gives low-latency round-trips.

## Repo boundaries

- `agent_core/event_service` owns: durable store (SQLite), `publish`, `subscribe`, `latest_seq`, replay.
- `agent_api/event.py` owns only the transport (SSE/WS framing + headers).
- All emitters (agent runtime, tools, sessions) call `event_service.publish` — never the SSE layer directly.