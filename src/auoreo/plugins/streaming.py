"""I7: stream a connector reply while the run is still writing it (spec §25.15 "Telegram
`sendMessageDraft` streaming").

When a connector plugin's settings say `stream: true`, `InboundRouter` wraps the run in a
`StreamRelay`: it listens to the session's `message.delta` events on the bus and, at most once
per `interval_s`, sends the text so far through the plugin's normal delivery with
`meta.stream = "partial"`. The final answer still goes out as an ordinary send; the plugin
turns the partials into drafts / message edits (and rate-limits on its own side too).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from auoreo.core.services import Services

Send = Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]]
RESET_TYPES = frozenset({"tool.started"})  # a tool call ends the current text segment


class StreamRelay:
    def __init__(
        self,
        services: Services,
        session_id: str,
        chat_id: str,
        send: Send,
        meta: dict[str, Any],
        *,
        interval_s: float = 1.0,
        min_chars: int = 20,
    ) -> None:
        self.s = services
        self.session_id = session_id
        self.chat_id = chat_id
        self.send = send
        self.meta = meta
        self.interval_s = interval_s
        self.min_chars = min_chars
        self.text = ""
        self.sent: list[str] = []
        self._task: asyncio.Task[None] | None = None
        self._sub: Any = None

    async def __aenter__(self) -> StreamRelay:
        self._sub = self.s.bus.subscribe(frozenset({f"session:{self.session_id}"}))
        self._task = asyncio.create_task(self._loop(), name=f"stream-relay:{self.session_id}")
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        if self._sub is not None:
            self.s.bus.unsubscribe(self._sub)

    async def _flush(self) -> bool:
        text = self.text.strip()
        if len(text) < self.min_chars or (self.sent and self.sent[-1] == text):
            return False
        self.sent.append(text)
        with contextlib.suppress(Exception):
            await self.send(self.chat_id, text, {**self.meta, "stream": "partial"})
        return True

    async def _loop(self) -> None:
        loop = asyncio.get_running_loop()
        last = -1e9
        while True:
            try:
                ev = await asyncio.wait_for(self._sub.queue.get(), self.interval_s)
            except TimeoutError:
                ev = None
            if ev is not None and ev.type == "message.delta":
                self.text += str(ev.data.get("text", ""))
            elif ev is not None and ev.type in RESET_TYPES and self.text:
                self.text += "\n"
            if loop.time() - last >= self.interval_s and await self._flush():
                last = loop.time()
