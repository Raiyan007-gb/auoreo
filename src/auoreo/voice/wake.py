"""The wake-word daemon (spec §13.6, §24.5, §25.5): opt-in continuous listening behind a wake hit.

Started by the I8 starter (`auoreo.core.i8_boot`) only when `i8.wake.enabled`; off by default.

- **Single instance**: `<home>/wake.lock` is created with `O_CREAT | O_EXCL` and holds
  `{pid, ts}`; a lock whose pid is dead is stale and replaced once. A second daemon (another
  server on the same home) reports status `locked` and never opens the microphone.
- **Capture**: the selected `audio_input` backend (`sounddevice`, lazy; `fake` by default)
  yields 16 kHz mono int16 chunks.
- **Detection**: every `hop_s` the last `window_s` of audio goes to the selected `wake`
  backend's `detect()` (`backends.wake = "sidecar"` proxies it to the voice media sidecar's
  `wake` method), awaited in line (the capture queue buffers meanwhile); `cooldown_s` after a hit.
- **Routing**: a hit's phrase picks `i8.wake.routes[phrase]` → `{agent, session_id, title}`
  (the default route otherwise); the session is created once per phrase and reused.
- **Continuous listening only after a hit**: the next utterance (VAD start within
  `listen_timeout_s`, endpoint after `voice_endpoint_silence_ms` of silence, ≤ `max_utterance_s`)
  is transcribed, filtered (hallucination / no-speech / echo) and submitted to the routed
  session; then the daemon goes back to waiting for the wake word.
- **Pause**: detection is suspended while a routed session's turn runs (the agent is
  answering / speaking) and while `pause()` is in effect.
- **Dead mic**: when the peak |sample| stays ≤ `dead_mic_peak` (10) for `dead_mic_window_s`
  (10 s) the status becomes `dead_mic` (`voice.wake_status`); it recovers on real signal.

Events: `voice.wake{phrase, score, session_id, agent}`, `voice.wake_status{status, detail}`,
`voice.transcript{source: "wake", session_id, text, duration_s}`, `voice.transcript_dropped`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from array import array
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

from auoreo.core.settings_i8 import WakeRoute, i8_of
from auoreo.tools.cu_lock import pid_alive
from auoreo.voice.audio import SAMPLE_RATE, wav_from_pcm

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)

PREROLL_MS = 300
CONFIRM_MS = 90


class WakeLocked(RuntimeError):
    pass


def acquire_lock(path: Path) -> None:
    """O_EXCL single-instance lock with PID liveness (stale lock replaced once)."""
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                holder = json.loads(path.read_text())
                pid = int(holder.get("pid", 0))
            except (OSError, ValueError, AttributeError):
                pid = 0
            if pid == os.getpid() or pid_alive(pid):
                raise WakeLocked(f"wake daemon already running (pid {pid})") from None
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
            continue
        with os.fdopen(fd, "w") as fh:
            json.dump({"pid": os.getpid(), "ts": time.time()}, fh)
        return
    raise WakeLocked(f"cannot take {path}")


def release_lock(path: Path) -> None:
    try:
        holder = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    if isinstance(holder, dict) and holder.get("pid") == os.getpid():
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def peak(pcm: bytes) -> int:
    a = array("h")
    a.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    return max((abs(v) for v in a), default=0)


class WakeDaemon:
    def __init__(self, services: Services, *, audio: Any = None) -> None:
        self.svc = services
        self.cfg = i8_of(services.settings).wake
        self.audio = audio
        self.status = "stopped"
        self.detail = ""
        self.hits = 0
        self.lock_path = Path(services.settings.home) / self.cfg.lock_name
        self._task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._window: deque[bytes] = deque()
        self._window_bytes = 0
        self._since_check = 0.0
        self._detecting = False
        self._cooldown_until = 0.0
        self._manual_pause = False
        self._locked = False
        self._peaks: deque[tuple[float, int]] = deque()
        self._peak_ms = 0.0
        self._sessions: dict[str, str] = {}
        # capture-after-hit state
        self._capturing = False
        self._capture_started = 0.0
        self._utt: bytearray | None = None
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._current = ""
        self._clock = 0.0
        """Audio time (seconds of PCM consumed): deterministic for fed test audio."""

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> bool:
        try:
            acquire_lock(self.lock_path)
        except WakeLocked as exc:
            await self._set_status("locked", str(exc))
            return False
        self._locked = True
        if self.audio is None:
            self.audio = self.svc.backend("audio_input")
        await self._set_status("listening")
        self._task = asyncio.get_running_loop().create_task(self._run(), name="voice:wake")
        return True

    async def close(self) -> None:
        if self.audio is not None:
            with contextlib.suppress(Exception):
                await self.audio.close()
        for t in [self._task, *self._tasks]:
            if t is not None:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
        self._task = None
        if self._locked:
            release_lock(self.lock_path)
            self._locked = False
        self.status = "stopped"

    def pause(self) -> None:
        self._manual_pause = True

    def resume(self) -> None:
        self._manual_pause = False

    @property
    def paused(self) -> bool:
        running = any(self.svc.coordinator.is_running(s) for s in self._sessions.values())
        return self._manual_pause or running

    def describe(self) -> dict[str, Any]:
        return {"status": self.status, "detail": self.detail, "hits": self.hits, "paused": self.paused}

    async def _set_status(self, status: str, detail: str = "") -> None:
        if status == self.status and detail == self.detail:
            return
        self.status, self.detail = status, detail
        await self.svc.emit("server", "voice.wake_status", {"status": status, "detail": detail})

    async def _run(self) -> None:
        try:
            async for chunk in self.audio.frames():
                await self.on_chunk(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("wake daemon: microphone failed: %s", exc)
            await self._set_status("error", str(exc)[:300])
            return
        await self._set_status("stopped", "microphone stream ended")

    # ---------------------------------------------------------------- audio
    async def on_chunk(self, chunk: bytes) -> None:
        if not chunk:
            return
        ms = len(chunk) / 2 / SAMPLE_RATE * 1000
        self._clock += ms / 1000
        await self._track_peak(ms, peak(chunk))
        if self._capturing:
            await self._capture(chunk, ms)
            return
        self._push_window(chunk)
        if self.paused:
            if self.status == "listening":
                await self._set_status("paused")
            return
        if self.status == "paused":
            await self._set_status("listening")
        self._since_check += ms / 1000
        if self._since_check < self.cfg.hop_s or self._detecting or self._clock < self._cooldown_until:
            return
        self._since_check = 0.0
        self._detecting = True
        await self._detect(b"".join(self._window))  # inline: the mic queue buffers meanwhile

    def _push_window(self, chunk: bytes) -> None:
        self._window.append(chunk)
        self._window_bytes += len(chunk)
        cap = int(self.cfg.window_s * SAMPLE_RATE) * 2
        while self._window and self._window_bytes - len(self._window[0]) >= cap:
            self._window_bytes -= len(self._window.popleft())

    async def _track_peak(self, ms: float, value: int) -> None:
        self._peaks.append((ms, value))
        self._peak_ms += ms
        while self._peaks and self._peak_ms - self._peaks[0][0] >= self.cfg.dead_mic_window_s * 1000:
            self._peak_ms -= self._peaks.popleft()[0]
        full = self._peak_ms >= self.cfg.dead_mic_window_s * 1000 - 1e-6
        dead = full and max(v for _, v in self._peaks) <= self.cfg.dead_mic_peak
        if dead and self.status != "dead_mic":
            await self._set_status(
                "dead_mic", f"peak ≤ {self.cfg.dead_mic_peak} for {self.cfg.dead_mic_window_s:g}s"
            )
        elif not dead and self.status == "dead_mic" and value > self.cfg.dead_mic_peak:
            await self._set_status("listening")

    async def _detect(self, pcm: bytes) -> None:
        try:
            hit = await self.svc.backend("wake").detect(wav_from_pcm(pcm, SAMPLE_RATE))
        except Exception as exc:
            log.warning("wake daemon: detect failed: %s", exc)
            await self._set_status("error", f"wake backend: {exc}"[:300])
            hit = None
        finally:
            self._detecting = False
        if hit is None or self._capturing:
            return
        await self._on_hit(hit.phrase, float(hit.score))

    async def _on_hit(self, phrase: str, score: float) -> None:
        self.hits += 1
        self._cooldown_until = self._clock + self.cfg.cooldown_s
        route = self._route(phrase)
        sid = await self._session_for(phrase, route)
        await self.svc.emit(
            sid or "server",
            "voice.wake",
            {"phrase": phrase, "score": round(score, 3), "session_id": sid, "agent": route.agent},
        )
        self._window.clear()
        self._window_bytes = 0
        self._capturing, self._capture_started = True, self._clock
        self._utt, self._speech_ms, self._silence_ms = None, 0.0, 0.0
        self._current = phrase
        await self._set_status("capturing")

    def _route(self, phrase: str) -> WakeRoute:
        key = " ".join(phrase.lower().split())
        return self.cfg.routes.get(key) or self.cfg.routes.get("default") or WakeRoute()

    async def _session_for(self, phrase: str, route: WakeRoute) -> str | None:
        if route.session_id:
            self._sessions[phrase] = route.session_id
            return route.session_id
        sid = self._sessions.get(phrase)
        if sid and await self.svc.get_session(sid) is not None:
            return sid
        sess = await self.svc.create_session(title=route.title, agent=route.agent)
        self._sessions[phrase] = sess.id
        return str(sess.id)

    # -------------------------------------------------------------- capture
    async def _capture(self, chunk: bytes, ms: float) -> None:
        speech = self.svc.ext["voice"].vad.is_speech(chunk, SAMPLE_RATE) if "voice" in self.svc.ext else False
        if self._utt is None:
            self._speech_ms = self._speech_ms + ms if speech else 0.0
            self._push_window(chunk)
            if self._speech_ms >= CONFIRM_MS:
                keep = int(PREROLL_MS / 1000 * SAMPLE_RATE) * 2
                self._utt = bytearray(b"".join(self._window)[-keep:])
                self._silence_ms = 0.0
            elif self._clock - self._capture_started >= self.cfg.listen_timeout_s:
                await self._end_capture("no speech after the wake word")
            return
        self._utt += chunk
        self._silence_ms = 0.0 if speech else self._silence_ms + ms
        too_long = len(self._utt) / 2 / SAMPLE_RATE >= self.cfg.max_utterance_s
        if self._silence_ms >= self.svc.settings.voice_endpoint_silence_ms or too_long:
            pcm = bytes(self._utt)
            await self._end_capture()
            self._spawn(self._utterance(self._current, pcm))

    async def _end_capture(self, why: str = "") -> None:
        self._capturing, self._utt = False, None
        self._window.clear()
        self._window_bytes = 0
        await self._set_status("listening", why)

    async def _utterance(self, phrase: str, pcm: bytes) -> None:
        voice = self.svc.ext.get("voice")
        sid = self._sessions.get(phrase)
        if voice is None or sid is None:
            return
        try:
            t = await voice.transcribe_wav(wav_from_pcm(pcm, SAMPLE_RATE))
        except Exception as exc:
            await self.svc.emit(
                sid, "voice.error", {"session_id": sid, "source": "wake", "message": str(exc)[:300]}
            )
            return
        judged = voice.judge(t, sid)
        if not judged.keep:
            data = {"session_id": sid, "source": "wake", "reason": judged.reason, "score": judged.score}
            await self.svc.emit(sid, "voice.transcript_dropped", data)
            return
        text = t.text.strip()
        duration = round(len(pcm) / 2 / SAMPLE_RATE, 3)
        data = {"session_id": sid, "source": "wake", "text": text, "duration_s": duration}
        await self.svc.emit(sid, "voice.transcript", data)
        running = self.svc.coordinator.is_running(sid)
        await self.svc.coordinator.submit(sid, text, "steer" if running else "followup")

    def _spawn(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Wait for in-flight detections/transcriptions (tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
