"""KittenTTS local TTS (Apache-2.0, ONNX, CPU). Imported lazily; the model is a singleton.

Default model `KittenML/kitten-tts-nano-0.8-int8` (25 MB, 24 kHz, voices Bella, Jasper, Luna,
Bruno, Rosie, Hugo, Kiki, Leo); `generate(text, voice, speed, clean_text=True)` returns float
samples at 24 kHz which are written as int16 WAV. Per-request cap 2000 chars (spec §25.3).
"""

from __future__ import annotations

import asyncio
import importlib.util
import threading
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.tts import Audio, TtsBackend, clamp_speed
from auoreo.voice.audio import AudioError, VoiceUnavailable, wav_from_pcm

MODEL = "KittenML/kitten-tts-nano-0.8-int8"
RATE = 24_000
DEFAULT_VOICE = "Jasper"
VOICES = ("Bella", "Jasper", "Luna", "Bruno", "Rosie", "Hugo", "Kiki", "Leo")
INSTALL = "pip install the KittenTTS wheel from https://github.com/KittenML/KittenTTS/releases"
_lock = threading.Lock()
_model: list[Any] = []


class Backend(TtsBackend):
    chunk_limit = 2000

    @property
    def download(self) -> bool:
        return bool(getattr(self.settings, "voice_download_models", False))

    async def check(self) -> BackendStatus:
        if importlib.util.find_spec("kittentts") is None:
            return BackendStatus(False, "kittentts is not installed", remedy=INSTALL)
        if self.download:
            return BackendStatus(True, f"{MODEL} (downloads on first use)")
        try:
            await asyncio.to_thread(self._cached)
        except Exception:
            return BackendStatus(
                False,
                f"{MODEL} is not downloaded",
                remedy='set "voice_download_models": true in settings.json (or pre-fetch the model)',
            )
        return BackendStatus(True, f"{MODEL} cached")

    @staticmethod
    def _cached() -> str:
        from huggingface_hub import snapshot_download

        return str(snapshot_download(MODEL, local_files_only=True))

    def _get(self) -> Any:
        with _lock:
            if not _model:
                if not self.download:
                    self._cached()  # raises when the weights are not in the HF cache
                from kittentts import KittenTTS

                _model.append(KittenTTS(MODEL))
            return _model[0]

    def _run(self, text: str, voice: str, speed: float) -> bytes:
        try:
            model = self._get()
        except ImportError:
            raise VoiceUnavailable(f"kittentts is not installed; {INSTALL}") from None
        except Exception as exc:
            raise VoiceUnavailable(
                f"cannot load {MODEL} ({type(exc).__name__}); set voice_download_models or pre-fetch it"
            ) from None
        try:
            import numpy as np

            samples = np.asarray(model.generate(text, voice=voice, speed=speed, clean_text=True)).reshape(-1)
            pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        except Exception as exc:
            raise AudioError(f"KittenTTS failed: {type(exc).__name__}: {exc}"[:300]) from None
        return wav_from_pcm(pcm, RATE)

    async def synthesize(self, text: str, *, voice: str | None = None, speed: float = 1.0) -> Audio:
        name = voice if voice in VOICES else DEFAULT_VOICE
        data = await asyncio.to_thread(self._run, text, name, clamp_speed(speed))
        return Audio(data, "audio/wav", RATE)
