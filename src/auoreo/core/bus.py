"""In-process fan-out. The `events` table is the source of truth; the bus only delivers.

Each subscriber owns a bounded queue. When it is full, coalescable events (deltas,
progress, pings) are dropped; for anything else the subscriber is marked `overflowed`
and its stream ends, so the client reconnects and replays from `Last-Event-ID`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from auoreo.schema.events import COALESCABLE, ServerEvent

log = logging.getLogger(__name__)
QUEUE_MAX = 256


@dataclass(eq=False)
class Subscriber:
    channels: frozenset[str] | None  # None = all channels
    queue: asyncio.Queue[ServerEvent] = field(default_factory=lambda: asyncio.Queue(QUEUE_MAX))
    overflowed: bool = False
    dropped: int = 0

    def wants(self, event: ServerEvent) -> bool:
        if self.channels is None:
            return True
        return f"session:{event.aggregate_id}" in self.channels or (
            event.aggregate_id == "server" and "server" in self.channels
        )


class EventBus:
    def __init__(self) -> None:
        self._subs: set[Subscriber] = set()
        self._taps: list[Callable[[ServerEvent], None]] = []

    def add_tap(self, fn: Callable[[ServerEvent], None]) -> None:
        """W1-E: observe locally originated events (the Redis bridge forwards them)."""
        self._taps.append(fn)

    def remove_tap(self, fn: Callable[[ServerEvent], None]) -> None:
        if fn in self._taps:
            self._taps.remove(fn)

    def subscribe(self, channels: frozenset[str] | None = None) -> Subscriber:
        sub = Subscriber(channels)
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subs.discard(sub)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def publish(self, event: ServerEvent, *, remote: bool = False) -> None:
        """`remote=True`: delivered from another worker; taps are skipped (no echo)."""
        if not remote:
            for tap in list(self._taps):
                try:
                    tap(event)
                except Exception:
                    log.exception("event bus tap failed")
        for sub in list(self._subs):
            if sub.overflowed or not sub.wants(event):
                continue
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                if event.type in COALESCABLE:
                    sub.dropped += 1
                    continue
                sub.overflowed = True
                log.warning("subscriber overflowed; it must reconnect and replay")
