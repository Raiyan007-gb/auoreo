"""The agent speaks back over WebRTC (F1, spec §13).

`RtcSpeaker` watches the bus (like `tools.shell_sessions` watches `session.archived`). On a
final assistant `message.completed` (content, no tool calls) for a session with attached RTC
peers that speak (`Peer.speech` set: voice enabled, `voice_speak_over_rtc`, no `speak: false`
in the offer, an audio m-line), the text is shaped with `prepare_spoken_text`, capped at
`voice_speak_max_chars` (cut at a sentence or word boundary, ending in "…"; the full answer
stays in the transcript), split into sentence chunks, synthesized chunk by chunk with the
selected TTS backend, normalized to 16 kHz mono and queued with `Peer.say`.

Events (on the session aggregate): `voice.speaking{rtc_id, session_id, chars, truncated}`
before the first chunk and `voice.spoke{rtc_id, session_id, chars, duration_s, backend,
cancelled}` once all audio is queued (or a barge-in cut it: `cancelled: true`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from typing import TYPE_CHECKING, Any

from auoreo.voice.audio import normalize, read_wav
from auoreo.voice.text import chunk_text, prepare_spoken_text

if TYPE_CHECKING:
    from auoreo.voice.service import VoiceService

log = logging.getLogger(__name__)

SENTENCE_CHUNK = 240
"""Chars per TTS request: small enough that the first sentence starts playing quickly."""
ELLIPSIS = "…"
_SENTENCE_END = re.compile(r"[.!?…](?=\s|$)")


def truncate_spoken(text: str, limit: int) -> tuple[str, bool]:
    """Cut `text` to ≤ `limit` chars (plus "…") at the last sentence end, else the last space."""
    if len(text) <= limit:
        return text, False
    head = text[:limit]
    ends = [m.end() for m in _SENTENCE_END.finditer(head)]
    cut = ends[-1] if ends and ends[-1] >= limit // 2 else head.rfind(" ")
    head = head[: cut if cut > 0 else limit].rstrip(" ,;:-")
    return head + ELLIPSIS, True


class RtcSpeaker:
    def __init__(self, voice: VoiceService, manager: Any) -> None:
        self.voice = voice
        self.svc = voice.svc
        self.manager = manager
        self._sub: Any = None
        self._watch_task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[Any]] = set()

    def start(self) -> bool:
        """Subscribe to the bus (idempotent). False when there is no running loop yet."""
        if self._watch_task is not None and not self._watch_task.done():
            return True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        if self._sub is None:
            self._sub = self.svc.bus.subscribe(None)  # before the task runs: no event is missed
        self._watch_task = loop.create_task(self._watch(), name="voice:rtc_speaker")
        return True

    async def _watch(self) -> None:
        bus = self.svc.bus
        while True:
            if self._sub is None or self._sub.overflowed:
                if self._sub is not None:
                    bus.unsubscribe(self._sub)
                self._sub = bus.subscribe(None)
            ev = await self._sub.queue.get()
            if ev.type != "message.completed":
                continue
            data = ev.data or {}
            text = data.get("content")
            if data.get("tool_calls") or not isinstance(text, str) or not text.strip():
                continue
            for peer in list(self.manager.peers.values()):
                if peer.session_id == ev.aggregate_id and peer.speech is not None:
                    self._spawn(self.speak(peer, text))

    def _spawn(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def speak(self, peer: Any, text: str) -> float:
        """Synthesize `text` onto `peer`'s outbound track; returns the seconds queued."""
        settings = self.svc.settings
        spoken, truncated = truncate_spoken(prepare_spoken_text(text), settings.voice_speak_max_chars)
        if not spoken or peer.speech is None:
            return 0.0
        agg = peer.session_id or "server"
        base = {"rtc_id": peer.rtc_id, "session_id": peer.session_id}
        gen = peer.speech.generation
        await self.svc.emit(agg, "voice.speaking", {**base, "chars": len(spoken), "truncated": truncated})
        queued, cancelled = 0.0, False
        try:
            tts = self.voice.tts
            for chunk in chunk_text(spoken, min(tts.chunk_limit, SENTENCE_CHUNK)):
                if peer.speech.generation != gen or peer.rtc_id not in self.manager.peers:
                    cancelled = True
                    break
                audio = await tts.synthesize(chunk, voice=settings.voice_tts_voice, speed=1.0)
                info = read_wav(await normalize(audio.data, audio.media_type))
                if peer.speech.generation != gen:
                    cancelled = True
                    break
                queued += peer.say(info.pcm, info.sample_rate)
        except Exception as exc:
            log.warning("rtc %s: speaking failed: %s", peer.rtc_id, exc)
            await self.svc.emit(agg, "voice.error", {**base, "message": str(exc)[:300]})
            return queued
        await self.svc.emit(
            agg,
            "voice.spoke",
            {
                **base,
                "chars": len(spoken),
                "duration_s": round(queued, 3),
                "backend": self.voice.backend_name("tts"),
                "cancelled": cancelled,
            },
        )
        return queued

    async def close(self) -> None:
        tasks = [t for t in [self._watch_task, *self._tasks] if t is not None]
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._watch_task = None
        if self._sub is not None:
            self.svc.bus.unsubscribe(self._sub)
            self._sub = None
