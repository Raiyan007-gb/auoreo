"""`/ws` — the WebSocket control plane (spec §5.2/§5.3).

Same `ServerEvent` envelope as SSE for durable events (replay from `last_seq`, then live
tail from the bus), plus an uplink for approvals, answers, interrupts, steering and
follow-ups. Auth and the browser Origin check run before `accept()`.

Tasks per connection: a reader (socket → inbox queue), the inbound dispatcher (this
handler's own task), an outbound pump (bus → socket) and a pinger. One send lock keeps
frames whole and ordered. Close codes: 4001 auth expired (`http.ws_auth_ttl_s`; the client
re-authenticates, ≤3 retries) · 4401 credentials revoked (the server password changed).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Cookie, Depends, Header, Query, WebSocket, WebSocketException
from pydantic import TypeAdapter, ValidationError
from starlette.websockets import WebSocketDisconnect

from auoreo.api.deps import token_matches
from auoreo.core.approvals import pending_dict, reply_with_grants
from auoreo.core.bus import Subscriber, invalid_channels
from auoreo.core.event_store import REPLAY_LIMIT
from auoreo.core.services import Services
from auoreo.runtime.inbox import InboxFull
from auoreo.schema.ws import (
    CLOSE_AUTH_EXPIRED,
    CLOSE_KEEPALIVE,
    CLOSE_POLICY,
    CLOSE_REVOKED,
    CLOSE_TRY_LATER,
    MAX_BATCH,
    ClientFrame,
    WsAck,
    WsConnected,
    WsControlRequest,
    WsControlResponse,
    WsError,
    WsHello,
    WsPermissionReply,
    WsPing,
    WsServerPong,
    WsSubscribe,
    WsSubscribed,
    WsUnsubscribe,
    WsUnsubscribed,
)

log = logging.getLogger(__name__)
router = APIRouter()

LOOPBACK_ORIGIN = re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$")
HELLO_WAIT_S = 1.0
"""How long to wait for an optional `hello` before falling back to query params."""
_CLIENT_FRAME: TypeAdapter[Any] = TypeAdapter(ClientFrame)
_INVALID = object()
_OVERSIZED = object()
FOLLOWUP_MEMORY = 256
"""Followup request ids remembered per connection (for `edit`)."""

Ack = tuple[bool, str | None]
ErrorSubtype = Literal[
    "unknown_type", "invalid_frame", "binary_dropped", "unauthorized", "not_found", "unknown_channel"
]


def origin_allowed(origin: str | None, extra: list[str]) -> bool:
    """Non-browser clients send no Origin. Browsers must be loopback or allow-listed."""
    return origin is None or bool(LOOPBACK_ORIGIN.match(origin)) or origin in extra


def parse_channels(raw: str | None) -> frozenset[str] | None:
    if not raw:
        return None
    return frozenset(c.strip() for c in raw.split(",") if c.strip()) or None


def _channels(value: list[str] | None) -> frozenset[str] | None:
    return None if value is None else frozenset(value)


def split_frames(text: str) -> list[Any]:
    """One JSON object per message; `\\n`-joined batches and JSON arrays of ≤32 frames are
    tolerated on read (an empty or larger array → one `_OVERSIZED` marker)."""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed if 0 < len(parsed) <= MAX_BATCH else [_OVERSIZED]
        return [parsed]
    except json.JSONDecodeError:
        if "\n" not in text:
            return [_INVALID]
    out: list[Any] = []
    for line in text.splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append(_INVALID)
    return out


async def ws_guard(
    websocket: WebSocket,
    authorization: Annotated[str | None, Header()] = None,
    token: Annotated[str | None, Query()] = None,
    auoreo_token: Annotated[str | None, Cookie()] = None,
    origin: Annotated[str | None, Header()] = None,
) -> Services:
    """Runs before `accept()`: a failure rejects the handshake with policy code 1008. This is
    the only place `?token=` is accepted (browsers cannot set headers on `new WebSocket()`)."""
    svc: Services = websocket.app.state.services
    if not origin_allowed(origin, svc.settings.cors_origins):
        raise WebSocketException(code=CLOSE_POLICY, reason="origin not allowed")
    if not token_matches(
        svc.settings.server_password, authorization=authorization, query_token=token, cookie=auoreo_token
    ):
        raise WebSocketException(code=CLOSE_POLICY, reason="invalid or missing token")
    websocket.scope.setdefault("state", {})["auoreo_password"] = svc.settings.server_password
    return svc


@router.websocket("/ws")
async def control_plane(
    websocket: WebSocket,
    svc: Annotated[Services, Depends(ws_guard)],
    last_seq: Annotated[int | None, Query(ge=0)] = None,
    channels: Annotated[str | None, Query()] = None,
) -> None:
    await websocket.accept()
    await WsConnection(websocket, svc).serve(last_seq, parse_channels(channels))


class WsConnection:
    def __init__(self, ws: WebSocket, svc: Services) -> None:
        self.conn_id = f"ws_{uuid.uuid4().hex[:12]}"
        self.ws = ws
        self.svc = svc
        self.sub: Subscriber | None = None
        self.channels: frozenset[str] | None = None
        self.cursor = 0
        self.closed = False
        self.tick = 0
        self.last_pong = time.monotonic()
        self.binary_dropped = 0
        self.authed_at = time.monotonic()
        self.password = (ws.scope.get("state") or {}).get("auoreo_password", svc.settings.server_password)
        self._followups: dict[str, tuple[str, str]] = {}  # request_id → (session_id, run_id)
        self._inbox: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._send_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task[None]] = []
        self._frames: dict[str, Callable[[Any], Awaitable[None]]] = {
            "hello": self._on_hello,
            "subscribe": self._on_subscribe,
            "unsubscribe": self._on_unsubscribe,
            "pong": self._on_pong,
            "ping": self._on_ping,
            "control_response": self._on_control_response,
            "control_request": self._on_control_request,
        }
        self._requests: dict[str, Callable[[Any], Awaitable[Ack]]] = {
            "interrupt": self._req_interrupt,
            "steer": self._req_steer,
            "followup": self._req_followup,
            "edit": self._req_edit,
        }

    # ------------------------------------------------------------ lifecycle
    async def serve(self, last_seq: int | None, channels: frozenset[str] | None) -> None:
        self.svc.connections.add(self)
        reader = asyncio.create_task(self._reader(), name=f"{self.conn_id}:reader")
        self._tasks.append(reader)
        try:
            first = await self._first_message()
            hello = _as_hello(first)
            if hello is not None:
                last_seq, channels = hello.last_seq, _channels(hello.channels)
            await self._subscribe(channels, last_seq, announce=True)
            if first is not None and hello is None:
                await self._handle(first)  # not a hello: process it as a normal frame
            self._tasks += [
                asyncio.create_task(self._pump(), name=f"{self.conn_id}:pump"),
                asyncio.create_task(self._pinger(), name=f"{self.conn_id}:ping"),
            ]
            while not self.closed and (msg := await self._inbox.get()) is not None:
                await self._handle(msg)
        except WebSocketDisconnect:
            pass
        finally:
            self.closed = True
            for t in self._tasks:
                t.cancel()
            for t in self._tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
            if self.sub is not None:
                self.svc.bus.unsubscribe(self.sub)
            self.svc.connections.remove(self)

    async def close(self, code: int, reason: str = "") -> None:
        if self.closed:
            return
        self.closed = True
        async with self._send_lock:
            with contextlib.suppress(Exception):
                await self.ws.close(code, reason)
        self._inbox.put_nowait(None)

    async def _reader(self) -> None:
        try:
            while True:
                msg = await self.ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                self._inbox.put_nowait(dict(msg))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            self.closed = True
            self._inbox.put_nowait(None)

    async def _first_message(self) -> dict[str, Any] | None:
        try:
            msg = await asyncio.wait_for(self._inbox.get(), HELLO_WAIT_S)
        except TimeoutError:
            return None
        if msg is None:
            raise WebSocketDisconnect(1000)
        return msg

    # ------------------------------------------------------------ outbound
    async def _send_unlocked(self, text: str) -> None:
        if self.closed:
            return
        try:
            await self.ws.send_text(text)
        except (WebSocketDisconnect, RuntimeError, OSError):
            self.closed = True
            self._inbox.put_nowait(None)

    async def send(self, frame: Any) -> None:
        async with self._send_lock:
            await self._send_unlocked(frame.model_dump_json())

    async def error(self, subtype: ErrorSubtype, message: str, request_id: str | None = None) -> None:
        await self.send(WsError(type="error", subtype=subtype, message=message, request_id=request_id))

    async def ack(self, request_id: str, ok: bool, detail: str | None = None) -> None:
        await self.send(WsAck(type="ack", request_id=request_id, ok=ok, detail=detail))

    async def _subscribe(
        self, channels: frozenset[str] | None, last_seq: int | None, *, announce: bool = False
    ) -> None:
        """Swap the bus subscription (subscribe first so nothing falls in the gap), then
        replay `seq > last_seq` in pages. No `last_seq` = live only from now."""
        svc = self.svc
        async with self._send_lock:
            old, self.sub = self.sub, svc.bus.subscribe(channels)
            self.channels = channels
            if old is not None:
                svc.bus.unsubscribe(old)
            server_seq = await svc.events.last_seq()
            if announce:
                await self._send_unlocked(self._connected(server_seq).model_dump_json())
            if last_seq is None:
                self.cursor = max(self.cursor, server_seq)
                return
            cursor = last_seq
            while True:
                batch = await svc.events.replay(cursor, channels)
                for ev in batch:
                    cursor = ev.seq
                    await self._send_unlocked(ev.model_dump_json())
                if len(batch) < REPLAY_LIMIT:
                    break
            self.cursor = cursor

    def _connected(self, server_seq: int) -> WsConnected:
        svc = self.svc
        return WsConnected(
            type="connected",
            server_seq=server_seq,
            generation=svc.harness.generation,
            pending_permissions=[pending_dict(p) for p in svc.approvals.pending()],
            pending_questions=[
                {"request_id": q.request_id, "session_id": q.session_id, "questions": q.questions}
                for q in svc.questions.pending()
            ],
        )

    async def _pump(self) -> None:
        while not self.closed:
            sub = self.sub
            assert sub is not None
            if sub.overflowed:
                await self.close(CLOSE_TRY_LATER, "backpressure: reconnect and replay from last seq")
                return
            try:
                ev = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            async with self._send_lock:
                if sub is not self.sub or ev.seq <= self.cursor:
                    continue
                self.cursor = ev.seq
                await self._send_unlocked(ev.model_dump_json())

    async def _pinger(self) -> None:
        interval = max(0.05, self.svc.settings.ws_ping_interval_s)
        while not self.closed:
            await asyncio.sleep(interval)
            if await self.check_auth():
                return
            if time.monotonic() - self.last_pong > 2 * interval:
                await self.close(CLOSE_KEEPALIVE, "no pong for two ping intervals")
                return
            self.tick += 1
            await self.send(WsPing(type="ping", tick=self.tick, server_seq=await self.svc.events.last_seq()))

    async def check_auth(self) -> bool:
        """Close with 4401 when the server password changed since the handshake (revoked) or
        4001 once `http.ws_auth_ttl_s` has passed (expired: reconnect and re-authenticate)."""
        st = self.svc.settings
        if st.server_password != self.password:
            await self.close(CLOSE_REVOKED, "credentials revoked")
            return True
        ttl = st.http.ws_auth_ttl_s
        if ttl is not None and time.monotonic() - self.authed_at > ttl:
            await self.close(CLOSE_AUTH_EXPIRED, "authentication expired; reconnect")
            return True
        return False

    # ------------------------------------------------------------- inbound
    async def _handle(self, msg: dict[str, Any]) -> None:
        text = msg.get("text")
        if text is None:
            self.binary_dropped += 1
            self.svc.connections.binary_dropped += 1
            log.warning("%s: dropped a binary frame (%d so far)", self.conn_id, self.binary_dropped)
            await self.error("binary_dropped", "binary frames are not supported; send JSON text frames")
            return
        for obj in split_frames(text):
            await self._handle_obj(obj)

    async def _handle_obj(self, obj: Any) -> None:
        if obj is _OVERSIZED:
            msg = f"invalid request (-32600): a batch is a non-empty JSON array of at most {MAX_BATCH} frames"
            await self.send(WsError(type="error", subtype="invalid_frame", message=msg, code=-32600))
            return
        if not isinstance(obj, dict):
            await self.error("invalid_frame", "each frame must be one JSON object")
            return
        rid = obj.get("request_id") if isinstance(obj.get("request_id"), str) else None
        kind = obj.get("type")
        handler = self._frames.get(kind) if isinstance(kind, str) else None
        if handler is None:
            await self.error("unknown_type", f"unknown frame type {kind!r}", rid)
            return
        try:
            frame = _CLIENT_FRAME.validate_python(obj)
        except ValidationError as exc:
            err = exc.errors()[0]
            where = ".".join(str(p) for p in err["loc"])
            await self.error("invalid_frame", f"{where}: {err['msg']}", rid)
            return
        try:
            await handler(frame)
        except Exception as exc:  # a bad request must never take the connection down
            log.exception("%s: %s frame failed", self.conn_id, kind)
            if rid is not None:
                await self.ack(rid, False, f"{type(exc).__name__}: {exc}")

    async def _bad_channels(self, channels: list[str] | None) -> bool:
        bad = invalid_channels(channels or [])
        if bad:
            await self.error("unknown_channel", f"unknown channel(s): {', '.join(sorted(bad))}")
        return bool(bad)

    async def _on_hello(self, frame: WsHello) -> None:
        if not await self._bad_channels(frame.channels):
            await self._subscribe(_channels(frame.channels), frame.last_seq, announce=True)

    async def _on_subscribe(self, frame: WsSubscribe) -> None:
        if await self._bad_channels(frame.channels):
            return
        await self._subscribe(_channels(frame.channels), frame.last_seq)
        chans = sorted(self.channels) if self.channels is not None else None
        server_seq = await self.svc.events.last_seq()
        await self.send(WsSubscribed(type="subscribed", channels=chans, server_seq=server_seq))

    async def _on_unsubscribe(self, frame: WsUnsubscribe) -> None:
        if await self._bad_channels(frame.channels):
            return
        if self.channels is None:
            await self.error(
                "invalid_frame", "subscribed to everything; subscribe to explicit channels first"
            )
            return
        removed = sorted(self.channels & set(frame.channels))
        remaining = self.channels - set(frame.channels)
        await self._subscribe(remaining, None)  # live only: nothing new to replay
        await self.send(WsUnsubscribed(type="unsubscribed", channels=removed, remaining=sorted(remaining)))

    async def _on_pong(self, _frame: Any) -> None:
        self.last_pong = time.monotonic()

    async def _on_ping(self, frame: Any) -> None:
        self.last_pong = time.monotonic()  # a pinging client is alive
        await self.send(WsServerPong(type="pong", tick=frame.tick, server_seq=self.cursor))

    async def _on_control_response(self, frame: WsControlResponse) -> None:
        r = frame.response
        if isinstance(r, WsPermissionReply):
            ok = await reply_with_grants(
                self.svc.approvals, self.svc.coordinator.engine_for, frame.request_id, r.reply, r.message
            )
        else:
            ok = await self.svc.questions.answer(frame.request_id, r.responses)
        await self.ack(frame.request_id, ok, None if ok else "no pending request with that id")

    async def _on_control_request(self, frame: WsControlRequest) -> None:
        req = frame.request
        if await self.svc.get_session(req.session_id) is None:
            await self.ack(frame.request_id, False, "session not found")
            return
        ok, detail = await self._requests[req.kind](req)
        if ok and req.kind == "followup" and detail and detail.startswith("run_"):
            self._followups[frame.request_id] = (req.session_id, detail)
            while len(self._followups) > FOLLOWUP_MEMORY:
                self._followups.pop(next(iter(self._followups)))
        await self.ack(frame.request_id, ok, detail)

    async def _req_interrupt(self, req: Any) -> Ack:
        cancelled = await self.svc.coordinator.cancel(req.session_id)
        return True, "cancelled" if cancelled else "not running"

    async def _req_steer(self, req: Any) -> Ack:
        try:
            run_id = await self.svc.coordinator.submit(req.session_id, req.text, "steer")
        except InboxFull as exc:
            return False, f"429 queue full: {exc}"
        return True, run_id or "steered"

    async def _req_followup(self, req: Any) -> Ack:
        try:
            run_id = await self.svc.coordinator.submit(
                req.session_id, req.text, agent=req.agent, model=req.model
            )
        except KeyError as exc:
            return False, str(exc).strip("'\"")
        except InboxFull as exc:
            return False, f"429 queue full: {exc}"
        return True, run_id or "handled"

    async def _req_edit(self, req: Any) -> Ack:
        """Rewrite a queued followup (by its request id or run id) before it starts."""
        sid, run_id = self._followups.get(req.target, (req.session_id, req.target))
        if sid != req.session_id:
            return False, "that request belongs to another session"
        rt = await self.svc.coordinator.runtime(req.session_id)
        if rt.inbox.edit(run_id, req.text):
            return True, run_id
        return False, "not queued (already running, finished or unknown)"


def _as_hello(msg: dict[str, Any] | None) -> WsHello | None:
    if msg is None or msg.get("text") is None:
        return None
    try:
        return WsHello.model_validate_json(msg["text"])
    except ValidationError:
        return None
