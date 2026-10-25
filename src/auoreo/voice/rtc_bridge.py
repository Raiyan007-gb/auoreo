"""Live transcription over WebRTC: VAD endpointing on `RtcManager.on_audio_frames`.

Every ~20 ms chunk of 16 kHz mono int16 audio from every peer goes through the selected VAD
backend's `is_speech`. Per peer:

  idle ──(speech ≥ 100 ms)──▶ speaking ──(silence ≥ voice_endpoint_silence_ms)──▶ utterance
                                  └──────────(30 s cap)──────────────────────────────▶ utterance

An utterance (300 ms pre-roll, 200 ms of trailing silence kept) becomes a WAV → STT backend → event
`voice.transcript{rtc_id, session_id, text}`; when the peer was opened for a session the text
is submitted to the coordinator as `steer` while a turn runs, else as a `followup`.

Barge-in: when speech starts while the peer's session has a running turn,
`voice.barge_in{rtc_id, session_id}` is emitted. There is no outbound TTS track yet, so there
is nothing to stop; once one exists, barge-in pauses it (spec §13.5) — a later item.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from auoreo.voice.audio import wav_from_pcm

if TYPE_CHECKING:
    from auoreo.voice.service import VoiceService

log = logging.getLogger(__name__)

CONFIRM_MS = 100
PREROLL_MS = 300
TAIL_MS = 200
MAX_UTTERANCE_S = 30.0


@dataclass
class _Peer:
    speaking: bool = False
    speech_ms: float = 0.0
    silence_ms: float = 0.0
    buf: bytearray = field(default_factory=bytearray)
    preroll: deque[bytes] = field(default_factory=deque)
    preroll_ms: float = 0.0


class RtcVoiceBridge:
    def __init__(self, voice: VoiceService) -> None:
        self.voice = voice
        self.svc = voice.svc
        self.peers: dict[str, _Peer] = {}
        self.manager: Any = None
        self._tasks: set[asyncio.Task[Any]] = set()

    def attach(self, rtc_manager: Any) -> None:
        self.manager = rtc_manager
        rtc_manager.on_audio_frames = self.on_frames

    # ------------------------------------------------------------- the hook
    def on_frames(self, rtc_id: str, session_id: str | None, pcm: Any, sample_rate: int) -> None:
        """Sync and cheap: VAD per chunk, state machine, spawn STT for finished utterances."""
        frame = pcm.tobytes() if hasattr(pcm, "tobytes") else bytes(pcm)
        if not frame:
            return
        ms = len(frame) / 2 / sample_rate * 1000
        st = self.peers.setdefault(rtc_id, _Peer())
        speech = self.voice.vad.is_speech(frame, sample_rate)
        if st.speaking:
            st.buf += frame
            st.silence_ms = 0.0 if speech else st.silence_ms + ms
            too_long = len(st.buf) / 2 / sample_rate >= MAX_UTTERANCE_S
            if st.silence_ms >= self.svc.settings.voice_endpoint_silence_ms or too_long:
                self._finish(rtc_id, session_id, st, sample_rate)
            return
        st.preroll.append(frame)
        st.preroll_ms += ms
        while st.preroll and st.preroll_ms - len(st.preroll[0]) / 2 / sample_rate * 1000 >= PREROLL_MS:
            st.preroll_ms -= len(st.preroll.popleft()) / 2 / sample_rate * 1000
        st.speech_ms = st.speech_ms + ms if speech else 0.0
        if st.speech_ms >= CONFIRM_MS:
            st.speaking, st.silence_ms = True, 0.0
            st.buf = bytearray(b"".join(st.preroll))
            st.preroll.clear()
            st.preroll_ms = 0.0
            self._speech_started(rtc_id, session_id)

    def _speech_started(self, rtc_id: str, session_id: str | None) -> None:
        if session_id and self.svc.coordinator.is_running(session_id):
            self._spawn(
                self.svc.emit(session_id, "voice.barge_in", {"rtc_id": rtc_id, "session_id": session_id})
            )

    def _finish(self, rtc_id: str, session_id: str | None, st: _Peer, rate: int) -> None:
        trim = int(max(0.0, st.silence_ms - TAIL_MS) / 1000 * rate) * 2  # drop the endpoint silence
        pcm = bytes(st.buf[: len(st.buf) - trim])
        st.speaking, st.speech_ms, st.silence_ms, st.buf = False, 0.0, 0.0, bytearray()
        self._spawn(self._utterance(rtc_id, session_id, pcm, rate))
        if self.manager is not None:  # forget peers that are gone
            for gone in [r for r in self.peers if r not in self.manager.peers]:
                self.peers.pop(gone, None)

    # --------------------------------------------------------- async work
    async def _utterance(self, rtc_id: str, session_id: str | None, pcm: bytes, rate: int) -> None:
        try:
            t = await self.voice.transcribe_wav(wav_from_pcm(pcm, rate))
        except Exception as exc:
            log.warning("rtc %s: utterance transcription failed: %s", rtc_id, exc)
            await self.svc.emit(
                session_id or "server",
                "voice.error",
                {"rtc_id": rtc_id, "session_id": session_id, "message": str(exc)[:300]},
            )
            return
        text = t.text.strip()
        if not text:
            return
        duration = round(len(pcm) / 2 / rate, 3)
        await self.svc.emit(
            session_id or "server",
            "voice.transcript",
            {"rtc_id": rtc_id, "session_id": session_id, "text": text, "duration_s": duration},
        )
        if session_id and await self.svc.get_session(session_id) is not None:
            running = self.svc.coordinator.is_running(session_id)
            await self.svc.coordinator.submit(session_id, text, "steer" if running else "followup")

    def _spawn(self, coro: Any) -> None:
        try:
            task = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:  # no loop (sync caller in a test): drop
            coro.close()
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        if self.manager is not None and self.manager.on_audio_frames == self.on_frames:
            self.manager.on_audio_frames = None
        for t in list(self._tasks):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self.peers.clear()
