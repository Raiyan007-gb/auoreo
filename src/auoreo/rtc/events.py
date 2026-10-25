"""The `auoreo-events` data-channel protocol.

Durable events travel in the same `ServerEvent` envelope as SSE and `/ws` (never a second
event model). The channel is a read-only mirror: `subscribe` and `ping` are the only
inbound frames; approvals, steering and interrupts are WebSocket-only (spec §19.12).

Server → client:
  `{"type":"connected","rtc_id":…,"server_seq":…}` once, when the channel opens;
  `ServerEvent` JSON objects; `{"type":"pong","tick":n}`; `{"type":"error",…}`.

Backpressure: while `bufferedAmount` exceeds the soft limit (1 MiB), coalescable events
(`message.delta`, `tool.progress`, `ping`) are dropped; any other event waits for the
buffer to drain. If it does not drain within `drain_timeout_s` (or the bus subscriber
overflows) the peer is closed with reason `backpressure`.

Nothing here imports aiortc: the channel is duck-typed so the protocol is unit-testable.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import ValidationError

from auoreo.core.bus import Subscriber
from auoreo.core.event_store import REPLAY_LIMIT
from auoreo.schema.events import COALESCABLE, ServerEvent
from auoreo.schema.ws import WsSubscribe

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)

CHANNEL_LABEL = "auoreo-events"
MAX_MESSAGE_BYTES = 128 * 1024
SOFT_BUFFER_BYTES = 1 << 20
DRAIN_TIMEOUT_S = 5.0
UNSUPPORTED = "control (approvals, steer, interrupt) is WebSocket-only"


class Channel(Protocol):
    """The slice of `aiortc.RTCDataChannel` this module uses."""

    @property
    def readyState(self) -> str: ...  # noqa: N802 — aiortc/W3C spelling

    @property
    def bufferedAmount(self) -> int: ...  # noqa: N802

    def send(self, data: str) -> None: ...


CloseRequest = Callable[[str], Coroutine[Any, Any, Any]]


class EventChannel:
    """One peer's events channel: an inbound dispatcher task and an outbound pump task."""

    def __init__(
        self,
        svc: Services,
        rtc_id: str,
        channel: Channel,
        request_close: CloseRequest,
        *,
        soft_limit: int = SOFT_BUFFER_BYTES,
        drain_timeout_s: float = DRAIN_TIMEOUT_S,
    ) -> None:
        self.svc = svc
        self.rtc_id = rtc_id
        self.channel = channel
        self.soft_limit = soft_limit
        self.drain_timeout_s = drain_timeout_s
        self.sub: Subscriber | None = None
        self.cursor = 0
        self.dropped = 0
        self.closed = False
        self._request_close = request_close
        self._close_task: asyncio.Task[Any] | None = None
        self._has_sub = asyncio.Event()
        self._lock = asyncio.Lock()
        self._inbox: asyncio.Queue[str | bytes | None] = asyncio.Queue()
        self._tasks: list[asyncio.Task[None]] = []
        self._frames: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {
            "subscribe": self._on_subscribe,
            "ping": self._on_ping,
        }

    # ------------------------------------------------------------ lifecycle
    async def start(self, session_id: str | None = None) -> None:
        """Announce, optionally auto-subscribe (live) to the offer's session, start tasks."""
        server_seq = await self.svc.events.last_seq()
        self.send_obj({"type": "connected", "rtc_id": self.rtc_id, "server_seq": server_seq})
        if session_id is not None:
            await self.subscribe(frozenset({f"session:{session_id}"}), None)
        self._tasks = [
            asyncio.create_task(self._dispatch(), name=f"{self.rtc_id}:dispatch"),
            asyncio.create_task(self._pump(), name=f"{self.rtc_id}:pump"),
        ]

    def feed(self, message: str | bytes) -> None:
        """`channel.on("message")` callback (sync): queue for the dispatcher."""
        if not self.closed:
            self._inbox.put_nowait(message)

    async def stop(self) -> None:
        self.closed = True
        self._inbox.put_nowait(None)
        self._has_sub.set()
        current = asyncio.current_task()
        tasks = [t for t in self._tasks if t is not current]
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        if self.sub is not None:
            self.svc.bus.unsubscribe(self.sub)
            self.sub = None

    def _close(self, reason: str) -> None:
        """Ask the manager to close the peer from a fresh task (never await it from a pump)."""
        if self.closed or self._close_task is not None:
            return
        self.closed = True
        self._close_task = asyncio.create_task(self._request_close(reason))

    # ------------------------------------------------------------ outbound
    def send_text(self, text: str) -> bool:
        if self.channel.readyState != "open":
            return False
        try:
            self.channel.send(text)
        except Exception:  # channel torn down under us
            log.debug("%s: send failed", self.rtc_id, exc_info=True)
            return False
        return True

    def send_obj(self, obj: dict[str, Any]) -> bool:
        return self.send_text(json.dumps(obj, separators=(",", ":")))

    def error(self, subtype: str, message: str) -> None:
        self.send_obj({"type": "error", "subtype": subtype, "message": message})

    def _send_event(self, ev: ServerEvent) -> None:
        text = ev.model_dump_json()
        if len(text.encode()) > MAX_MESSAGE_BYTES:
            self.send_obj(
                {
                    "type": "error",
                    "subtype": "too_large",
                    "seq": ev.seq,
                    "message": f"{ev.type} exceeds {MAX_MESSAGE_BYTES} bytes; fetch it via GET /api/events",
                }
            )
            return
        self.send_text(text)

    async def _drain(self) -> bool:
        """Wait until the channel buffer is under the soft limit; False = gave up."""
        deadline = time.monotonic() + self.drain_timeout_s
        while self.channel.bufferedAmount > self.soft_limit:
            if self.closed or time.monotonic() > deadline:
                return False
            await asyncio.sleep(0.01)
        return True

    async def _admit(self, ev: ServerEvent) -> bool:
        if self.channel.bufferedAmount <= self.soft_limit:
            return True
        if ev.type in COALESCABLE:
            self.dropped += 1
            return False
        if await self._drain():
            return True
        log.warning("%s: data channel not draining; closing (backpressure)", self.rtc_id)
        self._close("backpressure")
        return False

    async def subscribe(self, channels: frozenset[str] | None, last_seq: int | None) -> None:
        """Same semantics as the `/ws` subscribe: swap the bus subscription first (nothing
        falls in the gap), then replay `seq > last_seq` in pages. No `last_seq` = live only."""
        svc = self.svc
        async with self._lock:
            old, self.sub = self.sub, svc.bus.subscribe(channels)
            if old is not None:
                svc.bus.unsubscribe(old)
            self._has_sub.set()
            if last_seq is None:
                self.cursor = max(self.cursor, await svc.events.last_seq())
                return
            cursor = last_seq
            while not self.closed:
                batch = await svc.events.replay(cursor, channels)
                for ev in batch:
                    cursor = ev.seq
                    if await self._admit(ev):
                        self._send_event(ev)
                    if self.closed:
                        return
                if len(batch) < REPLAY_LIMIT:
                    break
            self.cursor = cursor

    async def _pump(self) -> None:
        while not self.closed:
            sub = self.sub
            if sub is None:
                await self._has_sub.wait()
                continue
            if sub.overflowed:
                self._close("backpressure")
                return
            try:
                ev = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            async with self._lock:
                if sub is not self.sub or ev.seq <= self.cursor:
                    continue
                self.cursor = ev.seq
                if await self._admit(ev):
                    self._send_event(ev)

    # ------------------------------------------------------------- inbound
    async def _dispatch(self) -> None:
        while (msg := await self._inbox.get()) is not None:
            try:
                await self._handle(msg)
            except Exception:  # a bad frame must never take the peer down
                log.exception("%s: inbound frame failed", self.rtc_id)

    async def _handle(self, msg: str | bytes) -> None:
        if isinstance(msg, bytes):
            self.error("binary_dropped", "binary messages are not supported; send JSON text")
            return
        if len(msg.encode()) > MAX_MESSAGE_BYTES:
            self.error("too_large", f"messages are limited to {MAX_MESSAGE_BYTES} bytes")
            return
        try:
            obj = json.loads(msg)
        except json.JSONDecodeError:
            obj = None
        if not isinstance(obj, dict):
            self.error("invalid_frame", "each message must be one JSON object")
            return
        kind = obj.get("type")
        handler = self._frames.get(kind) if isinstance(kind, str) else None
        if handler is None:
            self.error("unsupported", UNSUPPORTED)
            return
        await handler(obj)

    async def _on_subscribe(self, obj: dict[str, Any]) -> None:
        try:
            frame = WsSubscribe.model_validate(obj)
        except ValidationError as exc:
            err = exc.errors()[0]
            self.error("invalid_frame", f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}")
            return
        await self.subscribe(frozenset(frame.channels), frame.last_seq)

    async def _on_ping(self, obj: dict[str, Any]) -> None:
        tick = obj.get("tick")
        self.send_obj({"type": "pong", "tick": tick if isinstance(tick, int) else 0})
