"""Porcupine wake word (pvporcupine, lazy). Missing key → fail closed, never a silent fallback.

`pvporcupine.create(access_key, keywords | keyword_paths, sensitivities)`; `handle.process(frame)`
takes `handle.frame_length` int16 samples at `handle.sample_rate` (16 kHz) and returns the
keyword index (≥ 0) on a hit. `handle.delete()` frees the engine.
"""

from __future__ import annotations

import asyncio
import importlib.util
import threading
from pathlib import Path
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.wake import WakeBackend, WakeHit
from auoreo.voice.audio import VoiceUnavailable, read_wav
from auoreo.voice.cloud import secret
from auoreo.voice.cloud_stt import voice_g6

KEYS = ("PICOVOICE_ACCESS_KEY", "PORCUPINE_ACCESS_KEY")
INSTALL = "uv pip install 'auoreo[wake-porcupine]'"
KEY_REMEDY = "auoreo secrets set PICOVOICE_ACCESS_KEY (from console.picovoice.ai)"


class Backend(WakeBackend):
    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._handle: Any = None
        self._labels: list[str] = []
        self._lock = threading.Lock()

    def _key(self) -> str | None:
        return next((v for k in KEYS if (v := secret(self.settings, k))), None)

    def _targets(self) -> tuple[str, list[str]] | str:
        cfg = voice_g6(self.settings)
        if cfg.porcupine_keyword_paths:
            missing = [p for p in cfg.porcupine_keyword_paths if not Path(p).expanduser().is_file()]
            if missing:
                return f"keyword file(s) not found: {', '.join(missing)}"
            return "keyword_paths", [str(Path(p).expanduser()) for p in cfg.porcupine_keyword_paths]
        if not cfg.porcupine_keywords:
            return "no porcupine keywords configured"
        return "keywords", list(cfg.porcupine_keywords)

    async def check(self) -> BackendStatus:
        if importlib.util.find_spec("pvporcupine") is None:
            return BackendStatus(False, "pvporcupine is not installed", remedy=INSTALL)
        if not self._key():
            return BackendStatus(False, "PICOVOICE_ACCESS_KEY is not set (fail-closed)", remedy=KEY_REMEDY)
        targets = self._targets()
        if isinstance(targets, str):
            return BackendStatus(False, targets, remedy="fix settings.g6.voice.porcupine_*")
        return BackendStatus(True, f"porcupine {targets[0]}={len(targets[1])}")

    def _engine(self) -> Any:
        with self._lock:
            if self._handle is None:
                key = self._key()
                if not key:
                    raise VoiceUnavailable(f"wake/porcupine: no access key; {KEY_REMEDY}")
                targets = self._targets()
                if isinstance(targets, str):
                    raise VoiceUnavailable(f"wake/porcupine: {targets}")
                try:
                    import pvporcupine
                except ImportError:
                    raise VoiceUnavailable(f"pvporcupine is not installed; {INSTALL}") from None
                arg, values = targets
                sens = [voice_g6(self.settings).porcupine_sensitivity] * len(values)
                self._handle = pvporcupine.create(access_key=key, sensitivities=sens, **{arg: values})
                self._labels = [Path(v).stem if arg == "keyword_paths" else v for v in values]
            return self._handle

    def _detect(self, wav: bytes) -> WakeHit | None:
        import array

        handle = self._engine()
        info = read_wav(wav)
        if info.sample_rate != int(getattr(handle, "sample_rate", 16000)) or info.channels != 1:
            raise VoiceUnavailable("wake/porcupine needs 16 kHz mono int16 audio")
        samples = array.array("h", info.pcm[: len(info.pcm) // 2 * 2])
        n = int(handle.frame_length)
        for i in range(0, len(samples) - n + 1, n):
            idx = int(handle.process(samples[i : i + n].tolist()))
            if idx >= 0:
                label = self._labels[idx] if idx < len(self._labels) else self.phrase
                return WakeHit(label, start=i / info.sample_rate, score=1.0)
        return None

    async def detect(self, wav: bytes) -> WakeHit | None:
        return await asyncio.to_thread(self._detect, wav)

    async def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                self._handle.delete()
                self._handle = None
