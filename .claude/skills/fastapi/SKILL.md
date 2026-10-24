---
name: fastapi
description: Build the FastAPI single-port server — router composition, Server-Sent Events, WebSocket, dependency injection, static/UI serving, and OpenAPI. Use when building agent_api in this repo.
---

# FastAPI — one server on one port (agent_api)

Docs: https://fastapi.tiangolo.com · Context7: `/websites/fastapi_tiangolo`. This repo's invariant (plan.md Part C): **one FastAPI app, one port (default 4096), all clients** — TUI, web, SDK, CLI attach via HTTP + SSE + WS.

## Router composition (routes.py, like opencode protocol/api.ts)

```python
from fastapi import APIRouter, FastAPI

app = FastAPI(title="agent-platform", version="0.1.0")

health_router = APIRouter(prefix="/health", tags=["health"])


@health_router.get("")
async def health() -> dict:
    return {"status": "ok"}  # don't reuse the router's name for the handler


session_router = APIRouter(prefix="/session", tags=["session"])


@session_router.post("/create")  # decorate the router, not `app`
async def create_session(payload: SessionCreate) -> Session: ...


app.include_router(health_router)
app.include_router(session_router)
# ... one router per resource group (message, tool, model, provider, event, permission,
#     skill, command, agent, question) — single OpenAPI at /openapi.json
```

Follow the convention: `agent_api/api.py` holds the route-path/group definitions (the
contract), `handlers/` implement them, matching opencode's protocol-groups + server-handlers split.

## Server-Sent Events (SSE) — `GET /api/event`

**FastAPI ships native SSE** since **v0.135.0** — use `fastapi.sse` (`EventSourceResponse`,
`ServerSentEvent`) as the default. It auto-sets `Cache-Control: no-cache`, `X-Accel-Buffering: no`,
and a 15 s keep-alive `ping` comment out of the box. (FastAPI's bare `StreamingResponse` does none
of that — only use it if you need raw frame control.)

```python
from collections.abc import AsyncIterable
from typing import Annotated
from fastapi import Request, Header
from fastapi.sse import EventSourceResponse, ServerSentEvent


@app.get("/api/event", response_class=EventSourceResponse)
async def event_stream(
    request: Request,
    last_event_id: Annotated[int | None, Header()] = None,  # browser resends last `id` on reconnect
) -> AsyncIterable[ServerSentEvent]:
    # ⚠ MUST be a generator (yield), not `return EventSourceResponse(...)` — the native 15 s
    # keepalive lives in the route layer and is only injected when the handler yields the
    # async-iterable (fastapi/fastapi#15441). Returning the response directly bypasses it.
    subscriber = await event_service.subscribe(start_after=last_event_id)  # after auth
    try:
        async for event in event_generator(request, subscriber):
            yield event
    finally:
        await event_service.unsubscribe(subscriber)


async def event_generator(request: Request, subscriber: asyncio.Queue):
    try:
        while True:
            if await request.is_disconnected():
                break
            event = await subscriber.get()  # per-client queue; one per connection
            yield ServerSentEvent(data=event.data, event=event.type, id=str(event.seq), retry=3000)
    finally:
        cleanup()  # always release DB/Redis/queue in finally
```

- `ServerSentEvent(data=..., event=..., id=..., retry=..., comment=..., raw_data=...)` — `data` is
  always JSON-encoded (Pydantic models pass through); `raw_data` emits pre-formatted (non-JSON)
  payloads verbatim (mutually exclusive with `data`). Yielding a plain dict/model also works (sent as
  JSON `data:`). Declare return type `AsyncIterable[_]`. Works with any HTTP method (GET/POST).
- **Alternative (pin FastAPI < 0.135):** `sse-starlette` (`from sse_starlette.sse import EventSourceResponse, ServerSentEvent`).
  Not a pure import swap: there the handler must `return EventSourceResponse(gen())` (no
  `response_class` + `yield`), `ServerSentEvent` has no `raw_data`, plain strings go out as-is
  (native fastapi.sse JSON-quotes them), default `Cache-Control: no-store`, `ping=` interval (default 15 s).
- Fallback for very old stacks: raw `StreamingResponse(media_type="text/event-stream")` + manual
  `Cache-Control`/`X-Accel-Buffering` headers + your own ping.

**Multi-client fan-out:** one **per-client `asyncio.Queue`** per connection (a single shared queue
consumes once). Manage a `set` of queues under a lock; `pub` pushes to all; `finally` removes
the queue. This is the pattern to implement `agent_core/event_service` → `agent_api/event.py`.

**SSE header/keepalive checklist (production):**
- `Cache-Control: no-cache` + `X-Accel-Buffering: no` — set automatically by `fastapi.sse` (`sse-starlette` sets `X-Accel-Buffering: no` + `Cache-Control: no-store`).
- Keep-alive `ping` every ≤15–30 s (native 15 s default; sse-starlette `ping` param / comment frames) to survive proxy idle timeouts.
- Monotonic event IDs from a DB sequence/Redis INCR, **never `time.time()`** (plan.md Part G durable seq).
- `request.is_disconnected()` before blocking ops; `asyncio.CancelledError` must be re-raised after `finally` cleanup.
- Behind Nginx: `proxy_buffering off`, `proxy_read_timeout 3600s`, disable gzip on `text/event-stream`.

## WebSocket (low-latency bidirectional)

```python
@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()  # client→server (approvals, edits, cancel)
            await websocket.send_text(handle(data))  # server→client
    except WebSocketDisconnect:
        pass
```

## Static UI on the same port

```python
from fastapi.staticfiles import StaticFiles

app.mount("/", StaticFiles(directory="web/app/dist", html=True), name="ui")
# mount AFTER API routers so /api/* wins. html=True serves index.html for directories and
# 404.html on misses — it does NOT do SPA fallback; for client-side routes, subclass StaticFiles
# (catch 404 in get_response → serve index.html) or add a catch-all route.
```

Serve the built web app from the same server/port (plan.md Part C "one port" invariant). Keep
the UI under a sub-path or ensure router precedence so `/api/*` and `openapi.json` are not shadowed.

## Dependency injection + auth middleware

```python
from typing import Annotated
from fastapi import Depends, Header, HTTPException

def require_token(authorization: Annotated[str | None, Header()] = None):
    if not valid_bearer(authorization):   # OPENCODE-style server password/token; secrets.compare_digest
        raise HTTPException(status_code=401, detail="missing or invalid token")
    return authorization

@app.post("/permission", dependencies=[Depends(require_token)])
async def set_permission(...): ...
```

- Use `Depends` for auth, tenant/location (`x-workspace` header), and DB sessions; prefer the
  `Annotated[T, Depends(...)]` / `Annotated[T, Header()]` style (FastAPI's recommended form).
- Group-wide auth: `include_router(router, dependencies=[Depends(require_token)])` or
  `FastAPI(dependencies=[...])`. `fastapi.security.HTTPBearer(auto_error=False)` is an alternative
  that also documents the scheme in OpenAPI.
- Register exception handlers to map typed errors → HTTP codes (schema_error middleware, opencode-style).
- Pydantic request/response models come from `agent_schema` — never inline models in handlers (contract-first, plan.md Part K).
- CORS: enable only the configured origins (server `--cors`); bind default hostname `127.0.0.1`.