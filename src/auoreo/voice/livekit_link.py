"""A LiveKit room participant as an `RtcManager` peer (spec §26.6: both transports emit
identical events).

`LiveKitLink` registers a `rtc.peers.Peer` (rtc_id `lk_<hex>`) whose media plane is a LiveKit
room instead of an aiortc connection, so the existing voice plane works unchanged:

- inbound: `feed(pcm16k)` (called by the worker's `AgentSession` STT tap with 16 kHz mono int16
  audio of the remote participant) → `RtcManager.on_audio_frames` → `RtcVoiceBridge` (VAD
  endpointing, barge-in, echo guard, filter, `voice.transcript`, steer/followup);
- outbound: `RtcSpeaker` queues TTS with `Peer.say` onto the peer's `SpeechQueue`; a pump sends
  one 20 ms 48 kHz frame per tick (wall-clock paced, silence when idle) to a LiveKit
  `AudioSource` published as track `auoreo-voice`, feeding the echo reference as it plays;
- barge-in: `Peer.stop_speaking` clears the queue and also calls `interrupt()` (the worker maps
  it to `AgentSession.interrupt()` — the LiveKit side of `response.cancel`).

Bounds: an inbound chunk over `MAX_FRAME_BYTES` (128 KiB) is dropped and counted. The `rtc`
namespace (`AudioSource`, `AudioFrame`, `LocalAudioTrack`) is injected, so tests use a fake.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from auoreo.rtc.audio import SAMPLE_RATE
from auoreo.rtc.echo import EchoGuard
from auoreo.rtc.outbound import FRAME_MS, OUT_RATE, OUT_SAMPLES, SpeechQueue
from auoreo.rtc.peers import Peer

log = logging.getLogger(__name__)

MAX_FRAME_BYTES = 128 * 1024
TRACK_NAME = "auoreo-voice"


class LiveKitConnection:
    """Stands in for `RTCPeerConnection` on a LiveKit peer (`Peer.state`, `close()`)."""

    def __init__(self, room: str) -> None:
        self.room = room
        self.connectionState = "connected"  # noqa: N815 - mirrors aiortc's attribute

    async def close(self) -> None:
        self.connectionState = "closed"


class LiveKitLink:
    def __init__(
        self,
        services: Any,
        manager: Any,
        *,
        room: str,
        session_id: str | None,
        rtc: Any,
        publish: Callable[[Any], Awaitable[Any]] | None = None,
        interrupt: Callable[[], Any] | None = None,
        speak: bool = True,
    ) -> None:
        self.svc = services
        self.manager = manager
        self.rtc = rtc
        self.publish = publish
        self.interrupt = interrupt
        self.dropped_frames = 0
        self.source: Any = None
        self._pump: asyncio.Task[None] | None = None
        echo = EchoGuard()
        speaks = bool(speak and manager._speaks(None))
        self.peer = Peer(
            rtc_id=f"lk_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            pc=LiveKitConnection(room),
            has_audio=True,
            speech=SpeechQueue(echo) if speaks else None,
            echo=echo,
            manager=manager,
        )
        original = self.peer.stop_speaking

        def stop_speaking(reason: str = "client") -> int:
            dropped = original(reason)
            if self.interrupt is not None:
                with contextlib.suppress(Exception):
                    res = self.interrupt()
                    if asyncio.iscoroutine(res):
                        manager._spawn(res)
            return dropped

        self.peer.stop_speaking = stop_speaking  # type: ignore[method-assign]

    @property
    def rtc_id(self) -> str:
        return self.peer.rtc_id

    async def start(self) -> None:
        self.manager.peers[self.rtc_id] = self.peer
        if self.peer.speech is not None:
            self.source = self.rtc.AudioSource(OUT_RATE, 1)
            track = self.rtc.LocalAudioTrack.create_audio_track(TRACK_NAME, self.source)
            if self.publish is not None:
                await self.publish(track)
            self._pump = asyncio.get_running_loop().create_task(self._run_pump(), name=f"lk:{self.rtc_id}")
        await self.svc.emit(
            "server",
            "rtc.opened",
            {"rtc_id": self.rtc_id, "session_id": self.peer.session_id, "transport": "livekit"},
        )

    def feed(self, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> None:
        """Inbound 16 kHz mono int16 audio of the remote participant."""
        if len(pcm) > MAX_FRAME_BYTES:
            self.dropped_frames += 1
            return
        hook = self.manager.on_audio_frames
        if hook is not None and self.rtc_id in self.manager.peers:
            hook(self.rtc_id, self.peer.session_id, pcm, sample_rate)

    async def _run_pump(self) -> None:
        speech = self.peer.speech
        assert speech is not None
        origin = time.monotonic()
        sent = 0
        while True:
            frame = speech.next_frame()
            af = self.rtc.AudioFrame(frame, OUT_RATE, 1, OUT_SAMPLES)
            try:
                await self.source.capture_frame(af)
            except Exception as exc:  # the room went away
                log.info("livekit %s: capture_frame failed: %s", self.rtc_id, exc)
                return
            sent += 1
            delay = origin + sent * FRAME_MS / 1000 - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)

    async def close(self, reason: str = "left") -> None:
        if self._pump is not None:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._pump
            self._pump = None
        if self.rtc_id in self.manager.peers:
            await self.manager.close(self.rtc_id, reason)
