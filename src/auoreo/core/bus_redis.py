"""Multi-worker event fan-out over Redis pub/sub (spec L1045, opt-in `serve --workers N`).

Single process stays normative. With `redis_url` set, each worker runs one `RedisBridge`:
every locally originated event (an `EventBus` tap) is published to `auoreo:events` tagged
with this worker's id; remote events are re-published into the local bus with
`remote=True` (so they are never forwarded again), skipping our own worker id and any event
id already seen (bounded LRU). `state.db` stays the source of truth: SSE/WS replay reads it,
the bridge only carries live delivery. In `columns` mode the payload is encrypted on the wire
with the database key too.

What is still per process (hence sticky sessions at the load balancer): pending approvals and
questions (in-memory futures), WebSocket connections, session runtimes / run coordinator,
backend instances, RTC peers. The idempotency table is already shared (it lives in state.db).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections import OrderedDict
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from auoreo.core.bus import EventBus
from auoreo.schema.events import ServerEvent

if TYPE_CHECKING:
    from auoreo.core.crypto_columns import ColumnCipher
    from auoreo.core.diagnostics import Check
    from auoreo.core.services import Services
    from auoreo.core.settings import Settings

log = logging.getLogger(__name__)
CHANNEL = "auoreo:events"
REFUSE_MSG = "multi-worker needs redis_url for event fan-out; sticky sessions required at the load balancer"


class RedisBridge:
    def __init__(
        self,
        bus: EventBus,
        client: Any,
        *,
        cipher: ColumnCipher | None = None,
        worker_id: str | None = None,
        channel: str = CHANNEL,
        seen_max: int = 4096,
    ) -> None:
        self.bus = bus
        self.client = client
        self.cipher = cipher
        self.worker_id = worker_id or f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.channel = channel
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._seen_max = seen_max
        self._out: asyncio.Queue[ServerEvent] = asyncio.Queue(4096)
        self._tasks: list[asyncio.Task[None]] = []
        self._pubsub: Any = None
        self.forwarded = 0
        self.received = 0

    def _remember(self, event_id: str) -> bool:
        """True if new; keeps the last `seen_max` ids."""
        if event_id in self._seen:
            self._seen.move_to_end(event_id)
            return False
        self._seen[event_id] = None
        while len(self._seen) > self._seen_max:
            self._seen.popitem(last=False)
        return True

    def _on_local(self, event: ServerEvent) -> None:
        self._remember(event.id)
        try:
            self._out.put_nowait(event)
        except asyncio.QueueFull:
            log.warning("redis bridge backlog full; event %s not fanned out", event.id)

    async def start(self) -> None:
        self._pubsub = self.client.pubsub(ignore_subscribe_messages=True)
        await self._pubsub.subscribe(self.channel)
        self.bus.add_tap(self._on_local)
        self._tasks = [asyncio.create_task(self._write()), asyncio.create_task(self._read())]

    def _encode(self, event: ServerEvent) -> str:
        body = event.model_dump(mode="json")
        if self.cipher is not None and self.cipher.encrypting:
            body["data"] = self.cipher.encrypt(body["data"])
        return json.dumps({"w": self.worker_id, "e": body}, separators=(",", ":"))

    def _decode(self, raw: Any) -> ServerEvent | None:
        msg = json.loads(raw)
        if msg.get("w") == self.worker_id:
            return None
        body = msg["e"]
        if self.cipher is not None:
            body["data"] = self.cipher.decrypt(body.get("data"))
        event = ServerEvent.model_validate(body)
        return event if self._remember(event.id) else None

    async def _write(self) -> None:
        while True:
            event = await self._out.get()
            try:
                await self.client.publish(self.channel, self._encode(event))
                self.forwarded += 1
            except Exception as exc:  # Redis down: live fan-out degrades, replay still works
                log.warning("redis publish failed: %s", exc)

    async def _read(self) -> None:
        while True:
            try:
                msg = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("redis subscribe failed: %s; retrying", exc)
                await asyncio.sleep(1.0)
                continue
            if not msg or msg.get("type") != "message":
                continue
            try:
                event = self._decode(msg["data"])
            except Exception:
                log.exception("bad message on %s", self.channel)
                continue
            if event is not None:
                self.received += 1
                self.bus.publish(event, remote=True)

    async def close(self) -> None:
        self.bus.remove_tap(self._on_local)
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        if self._pubsub is not None:
            try:
                await self._pubsub.unsubscribe(self.channel)
                await self._pubsub.aclose()
            except Exception:
                log.debug("pubsub close failed", exc_info=True)
        try:
            await self.client.aclose()
        except Exception:
            log.debug("redis close failed", exc_info=True)


async def start(services: Services) -> RedisBridge | None:
    """Extension starter (→ services.ext["bus_redis"]); a no-op without `redis_url`."""
    url = services.settings.redis_url
    if not url:
        return None
    import redis.asyncio as aioredis

    client = aioredis.from_url(url)
    await asyncio.wait_for(client.ping(), 2.0)
    bridge = RedisBridge(services.bus, client, cipher=services.db.cipher)
    await bridge.start()
    log.info("redis event bridge up (worker %s, %s)", bridge.worker_id, safe_url(url))
    return bridge


def safe_url(url: str) -> str:
    """scheme://host:port/db without credentials (for logs and doctor)."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}{parts.path}"


async def doctor_checks(st: Settings) -> list[Check]:
    from auoreo.core.diagnostics import Check

    if not st.redis_url:
        if st.workers > 1:
            return [Check("workers", "error", f"workers={st.workers}; redis_url not set", REFUSE_MSG)]
        return [Check("workers", "ok", "workers=1 (single process); redis_url not set")]
    detail = f"workers={st.workers}; redis={safe_url(st.redis_url)}"
    try:
        import redis.asyncio as aioredis
    except ImportError:
        return [Check("workers", "error", detail + "; redis not installed", "install the `redis` extra")]
    client = aioredis.from_url(st.redis_url)
    try:
        await asyncio.wait_for(client.ping(), 2.0)
        return [Check("workers", "ok", detail + "; ping ok (sticky sessions required at the LB)")]
    except Exception as exc:
        return [Check("workers", "error", f"{detail}; ping failed ({exc})", "start Redis or fix redis_url")]
    finally:
        await client.aclose()
