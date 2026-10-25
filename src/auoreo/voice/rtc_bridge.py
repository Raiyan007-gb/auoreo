"""Live transcription over WebRTC: VAD endpointing on `RtcManager.on_audio_frames`, plus
barge-in and the echo guard while the agent speaks (F1).

Every ~20 ms chunk of 16 kHz mono int16 audio from every peer goes through the selected VAD
backend's `is_speech`. Per peer, while the agent is NOT speaking:

  idle ──(speech ≥ 100 ms)──▶ speaking ──(silence ≥ voice_endpoint_silence_ms)──▶ utterance
                                  └──────────(30 s cap)──────────────────────────────▶ utterance

An utterance (300 ms pre-roll, 200 ms of trailing silence kept) becomes a WAV → STT backend → event
`voice.transcript{rtc_id, session_id, text}`; when the peer was opened for a session the text
is submitted to the coordinator as `steer` while a turn runs, else as a `followup`.

Echo guard (`auoreo.rtc.echo`): while the peer's outbound track plays and for 300 ms after,
the speech threshold is `voice_speaking_rms × voice_echo_margin`; a frame whose normalised
correlation with the last 1 s of outbound audio exceeds `voice_echo_corr` is replaced by
silence (it is our own voice coming back).

Barge-in while the agent speaks (Hermes `_BargeDetector`, spec §25.5): normal speech starts
are suspended; the user barges in when ≥ 80 % of the last 300 ms is above the (raised)
threshold, no earlier than 500 ms after the speech started (so the agent's own onset is not
mistaken). Then `Peer.stop_speaking()` cuts the output (`voice.speech_cancelled`),
`voice.barge_in{cancelled: true}` is emitted, the utterance starts with 1200 ms of pre-roll,
the running turn is cancelled only when `voice_barge_in_interrupts_turn`, and the note
`SPEECH_INTERRUPTED_NOTE` is queued once (TTL 120 s) for the session's next user text: as a
`SessionState.pending_user_messages` entry (flushed by the runner after the current tool
round), or — if still pending — appended to the next transcribed utterance instead.

Speech that starts while a turn runs but nothing is being spoken emits
`voice.barge_in{cancelled: false}` (the utterance becomes a steer).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from auoreo.voice.audio import rms, wav_from_pcm

if TYPE_CHECKING:
    from auoreo.voice.service import VoiceService
    from auoreo.voice.speaker import RtcSpeaker

log = logging.getLogger(__name__)

CONFIRM_MS = 100
PREROLL_MS = 300
TAIL_MS = 200
MAX_UTTERANCE_S = 30.0
# Barge-in (Hermes `_BargeDetector`)
BARGE_WINDOW_MS = 300
BARGE_TRIP = 0.8
BARGE_GRACE_S = 0.5
BARGE_PREROLL_MS = 1200
SPEECH_INTERRUPTED_NOTE = "[the user interrupted your spoken reply]"
NOTE_TTL_S = 120.0


@dataclass
class _Peer:
    speaking: bool = False
    speech_ms: float = 0.0
    silence_ms: float = 0.0
    buf: bytearray = field(default_factory=bytearray)
    preroll: deque[bytes] = field(default_factory=deque)
    preroll_ms: float = 0.0
    window: deque[tuple[float, bool]] = field(default_factory=deque)
    echo_dropped: int = 0


def _tail(frames: deque[bytes], ms: float, rate: int) -> bytes:
    """The last `ms` of audio in `frames`."""
    want = int(ms / 1000 * rate) * 2
    out: list[bytes] = []
    have = 0
    for f in reversed(frames):
        if have >= want:
            break
        out.append(f)
        have += len(f)
    joined = b"".join(reversed(out))
    return joined[max(0, len(joined) - want) :]


class RtcVoiceBridge:
    def __init__(self, voice: VoiceService) -> None:
        self.voice = voice
        self.svc = voice.svc
        self.peers: dict[str, _Peer] = {}
        self.manager: Any = None
        self.speaker: RtcSpeaker | None = None
        self.notes: dict[str, tuple[float, list[str]]] = {}
        """session_id → (queued at, the runtime's pending_user_messages list)."""
        self._tasks: set[asyncio.Task[Any]] = set()

    def attach(self, rtc_manager: Any) -> None:
        from auoreo.voice.speaker import RtcSpeaker

        self.manager = rtc_manager
        rtc_manager.on_audio_frames = self.on_frames
        if self.speaker is None:
            self.speaker = RtcSpeaker(self.voice, rtc_manager)
        self.speaker.manager = rtc_manager
        self.speaker.start()

    # ------------------------------------------------------------- the hook
    def on_frames(self, rtc_id: str, session_id: str | None, pcm: Any, sample_rate: int) -> None:
        """Sync and cheap: echo guard + VAD per chunk, state machine, spawn STT for utterances."""
        frame = pcm.tobytes() if hasattr(pcm, "tobytes") else bytes(pcm)
        if not frame:
            return
        ms = len(frame) / 2 / sample_rate * 1000
        st = self.peers.setdefault(rtc_id, _Peer())
        if self.speaker is not None and self.manager is not None:
            self.speaker.start()  # attached before a loop existed
        peer = self.manager.peers.get(rtc_id) if self.manager is not None else None
        frame, speech, loud = self._classify(st, peer, frame, sample_rate)
        if st.speaking:
            st.buf += frame
            st.silence_ms = 0.0 if speech else st.silence_ms + ms
            too_long = len(st.buf) / 2 / sample_rate >= MAX_UTTERANCE_S
            if st.silence_ms >= self.svc.settings.voice_endpoint_silence_ms or too_long:
                self._finish(rtc_id, session_id, st, sample_rate)
            return
        st.preroll.append(frame)
        st.preroll_ms += ms
        while st.preroll and st.preroll_ms - len(st.preroll[0]) / 2 / sample_rate * 1000 >= BARGE_PREROLL_MS:
            st.preroll_ms -= len(st.preroll.popleft()) / 2 / sample_rate * 1000
        if peer is not None and peer.speaking:
            st.speech_ms = 0.0
            if self._barge_tripped(st, peer, ms, loud):
                self._start_utterance(st, BARGE_PREROLL_MS, sample_rate)
                peer.stop_speaking("barge_in")  # cut the output now, not after a loop hop
                self._spawn(self._barge_in(peer, rtc_id, session_id))
            return
        st.window.clear()
        st.speech_ms = st.speech_ms + ms if speech else 0.0
        if st.speech_ms >= CONFIRM_MS:
            self._start_utterance(st, PREROLL_MS, sample_rate)
            self._speech_started(rtc_id, session_id)

    def _classify(self, st: _Peer, peer: Any, frame: bytes, rate: int) -> tuple[bytes, bool, bool]:
        """`(frame, speech, loud)` after the echo guard (an echo frame becomes silence)."""
        settings = self.svc.settings
        echo = getattr(peer, "echo", None)
        now = time.monotonic()
        if echo is not None and echo.is_echo(frame, settings.voice_echo_corr, now):
            st.echo_dropped += 1
            return bytes(len(frame)), False, False
        guarded = echo is not None and echo.active(now)
        threshold = settings.voice_speaking_rms * (settings.voice_echo_margin if guarded else 1.0)
        loud = rms(frame) > threshold
        speech = self.voice.vad.is_speech(frame, rate) and (loud or not guarded)
        return frame, speech, loud

    @staticmethod
    def _barge_tripped(st: _Peer, peer: Any, ms: float, loud: bool) -> bool:
        st.window.append((ms, loud))
        total = sum(m for m, _ in st.window)
        while st.window and total - st.window[0][0] >= BARGE_WINDOW_MS:
            total -= st.window.popleft()[0]
        started = peer.speech.started_at
        if started is None or time.monotonic() - started < BARGE_GRACE_S:
            return False
        if total < BARGE_WINDOW_MS - 1e-6:
            return False
        return sum(m for m, hot in st.window if hot) / total >= BARGE_TRIP

    @staticmethod
    def _start_utterance(st: _Peer, preroll_ms: float, rate: int) -> None:
        st.speaking, st.silence_ms = True, 0.0
        st.buf = bytearray(_tail(st.preroll, preroll_ms, rate))
        st.preroll.clear()
        st.preroll_ms = 0.0
        st.window.clear()

    def _speech_started(self, rtc_id: str, session_id: str | None) -> None:
        if session_id and self.svc.coordinator.is_running(session_id):
            data = {"rtc_id": rtc_id, "session_id": session_id, "cancelled": False}
            self._spawn(self.svc.emit(session_id, "voice.barge_in", data))

    async def _barge_in(self, peer: Any, rtc_id: str, session_id: str | None) -> None:
        peer.stop_speaking("barge_in")  # idempotent (no-op when already cut)
        interrupt = bool(session_id) and self.svc.settings.voice_barge_in_interrupts_turn
        await self.svc.emit(
            session_id or "server",
            "voice.barge_in",
            {"rtc_id": rtc_id, "session_id": session_id, "cancelled": True, "interrupted_turn": interrupt},
        )
        if not session_id or await self.svc.get_session(session_id) is None:
            return
        if interrupt:
            await self.svc.coordinator.cancel(session_id)
        await self._queue_note(session_id)

    # ---------------------------------------------------- interrupted note
    async def _queue_note(self, session_id: str) -> None:
        rt = await self.svc.coordinator.runtime(session_id)
        pending: list[str] = rt.state.pending_user_messages
        if SPEECH_INTERRUPTED_NOTE not in pending:
            pending.append(SPEECH_INTERRUPTED_NOTE)
        stamp = time.monotonic()
        self.notes[session_id] = (stamp, pending)
        asyncio.get_running_loop().call_later(NOTE_TTL_S, self._expire_note, session_id, stamp)

    def _expire_note(self, session_id: str, stamp: float) -> None:
        entry = self.notes.get(session_id)
        if entry is None or entry[0] != stamp:
            return
        self.notes.pop(session_id, None)
        with contextlib.suppress(ValueError):
            entry[1].remove(SPEECH_INTERRUPTED_NOTE)

    def take_note(self, session_id: str) -> bool:
        """True (once) when the note is still pending and fresh: the caller appends it."""
        entry = self.notes.pop(session_id, None)
        if entry is None:
            return False
        stamp, pending = entry
        if SPEECH_INTERRUPTED_NOTE not in pending:
            return False  # the runner already delivered it after a tool round
        pending.remove(SPEECH_INTERRUPTED_NOTE)
        return time.monotonic() - stamp <= NOTE_TTL_S

    # ------------------------------------------------------------ utterance
    def _finish(self, rtc_id: str, session_id: str | None, st: _Peer, rate: int) -> None:
        trim = int(max(0.0, st.silence_ms - TAIL_MS) / 1000 * rate) * 2  # drop the endpoint silence
        pcm = bytes(st.buf[: len(st.buf) - trim])
        st.speaking, st.speech_ms, st.silence_ms, st.buf = False, 0.0, 0.0, bytearray()
        self._spawn(self._utterance(rtc_id, session_id, pcm, rate))
        if self.manager is not None:  # forget peers that are gone
            for gone in [r for r in self.peers if r not in self.manager.peers]:
                self.peers.pop(gone, None)

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
            if self.take_note(session_id):
                text = f"{text}\n\n{SPEECH_INTERRUPTED_NOTE}"
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
        if self.speaker is not None:
            await self.speaker.close()
        for t in list(self._tasks):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self.peers.clear()
