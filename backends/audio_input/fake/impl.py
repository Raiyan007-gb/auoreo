"""Test microphone: chunks fed by the test (`feed`, `feed_wav`) come out of `frames()`."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.audio_input import FRAME_MS, SAMPLE_RATE, AudioInputBackend
from auoreo.voice.audio import read_wav


class Backend(AudioInputBackend):
    is_test_backend = True

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._q: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.opened = 0

    async def check(self) -> BackendStatus:
        return BackendStatus(True, "fake microphone (test backend; fed PCM)")

    def feed(self, pcm: bytes, frame_ms: int = FRAME_MS) -> None:
        step = SAMPLE_RATE * frame_ms // 1000 * 2
        for i in range(0, len(pcm), step):
            self._q.put_nowait(pcm[i : i + step])

    def feed_wav(self, wav: bytes, frame_ms: int = FRAME_MS) -> None:
        self.feed(read_wav(wav).pcm, frame_ms)

    def end(self) -> None:
        self._q.put_nowait(None)

    async def frames(self, frame_ms: int = FRAME_MS) -> AsyncIterator[bytes]:
        self.opened += 1
        while True:
            chunk = await self._q.get()
            if chunk is None:
                return
            yield chunk

    async def close(self) -> None:
        self.end()
