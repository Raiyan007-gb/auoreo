"""System microphone via sounddevice (PortAudio). Imported lazily.

`frames()` opens one persistent `RawInputStream(samplerate=16000, channels=1, dtype="int16")`;
the PortAudio callback thread hands each block to the event loop with
`call_soon_threadsafe` into a bounded queue (oldest dropped when the consumer lags, never
blocking the audio thread). `check()` only queries the default input device (5 s timeout).
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
from collections.abc import AsyncIterator
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.audio_input import FRAME_MS, SAMPLE_RATE, AudioInputBackend
from auoreo.voice.audio import VoiceUnavailable

INSTALL = "uv pip install sounddevice   (Linux: apt install libportaudio2)"
QUEUE_MAX = 200


def default_input() -> dict[str, Any]:
    import sounddevice as sd

    info = sd.query_devices(kind="input")
    return dict(info) if not isinstance(info, dict) else info


class Backend(AudioInputBackend):
    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._stream: Any = None
        self._closed = asyncio.Event()

    async def check(self) -> BackendStatus:
        if importlib.util.find_spec("sounddevice") is None:
            return BackendStatus(False, "sounddevice is not installed", remedy=INSTALL)
        try:
            info = await asyncio.wait_for(asyncio.to_thread(default_input), 5)
        except Exception as exc:  # PortAudio missing, no device, permission denied
            return BackendStatus(
                False, f"no usable input device ({type(exc).__name__}: {exc})"[:200], INSTALL
            )
        return BackendStatus(True, f"input device {info.get('name', '?')!r}")

    async def frames(self, frame_ms: int = FRAME_MS) -> AsyncIterator[bytes]:
        try:
            import sounddevice as sd
        except (ImportError, OSError) as exc:
            raise VoiceUnavailable(f"audio_input/sounddevice: {exc}; {INSTALL}") from None
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=QUEUE_MAX)

        def put(data: bytes) -> None:
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(data)

        def callback(indata: Any, _frames: int, _time: Any, _status: Any) -> None:
            loop.call_soon_threadsafe(put, bytes(indata))

        self._closed.clear()
        self._stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=SAMPLE_RATE * frame_ms // 1000,
            callback=callback,
        )
        self._stream.start()
        try:
            while not self._closed.is_set():
                try:
                    yield await asyncio.wait_for(q.get(), 1.0)
                except TimeoutError:
                    continue
        finally:
            await self.close()

    async def close(self) -> None:
        self._closed.set()
        stream, self._stream = self._stream, None
        if stream is not None:
            with contextlib.suppress(Exception):
                stream.stop()
                stream.close()
