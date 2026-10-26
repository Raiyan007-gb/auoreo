"""NeuTTS local TTS (spec §25.3 licence correction). Imported lazily; the model is a singleton.

Only `neuphonic/neutts-air` is Apache-2.0; nano/2E ship under the NeuTTS Open License (NC-like)
and are refused unless `i8.tts.neutts_nc_opt_in` is set. NeuTTS clones a voice from a short
reference clip: `i8.tts.neutts_ref_audio` (WAV) + `i8.tts.neutts_ref_text` (its transcript);
the reference is encoded once (`encode_reference`) and reused. `infer(text, ref_codes,
ref_text)` returns float samples at 24 kHz, written as int16 WAV. Cap 2000 chars per request.
The Perth watermark NeuTTS adds is left intact.
"""

from __future__ import annotations

import asyncio
import importlib.util
import threading
from pathlib import Path
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.tts import Audio, TtsBackend
from auoreo.core.settings_i8 import i8_of
from auoreo.voice.audio import AudioError, VoiceUnavailable, wav_from_pcm

RATE = 24_000
APACHE_MODELS = frozenset(
    {"neuphonic/neutts-air", "neuphonic/neutts-air-q4-gguf", "neuphonic/neutts-air-q8-gguf"}
)
INSTALL = "uv pip install neutts-air   (plus espeak-ng on PATH)"
_lock = threading.Lock()
_cache: dict[str, Any] = {}


class Backend(TtsBackend):
    chunk_limit = 2000

    def _problem(self) -> tuple[str, str] | None:
        cfg = i8_of(self.settings).tts
        if cfg.neutts_model not in APACHE_MODELS and not cfg.neutts_nc_opt_in:
            return (
                f"{cfg.neutts_model} is under the NeuTTS Open License (non-commercial)",
                "use neuphonic/neutts-air or set i8.tts.neutts_nc_opt_in after reading the licence",
            )
        if importlib.util.find_spec("neuttsair") is None:
            return "neuttsair is not installed", INSTALL
        if not cfg.neutts_ref_audio or not Path(cfg.neutts_ref_audio).is_file() or not cfg.neutts_ref_text:
            return "no reference voice", "set i8.tts.neutts_ref_audio (WAV) and i8.tts.neutts_ref_text"
        return None

    async def check(self) -> BackendStatus:
        problem = self._problem()
        if problem is not None:
            return BackendStatus(False, problem[0], remedy=problem[1])
        return BackendStatus(True, f"{i8_of(self.settings).tts.neutts_model} (local, 24 kHz)")

    def _model(self) -> tuple[Any, Any]:
        cfg = i8_of(self.settings).tts
        key = f"{cfg.neutts_model}|{cfg.neutts_ref_audio}"
        with _lock:
            if key not in _cache:
                from neuttsair.neutts import NeuTTSAir

                tts = NeuTTSAir(
                    backbone_repo=cfg.neutts_model,
                    backbone_device="cpu",
                    codec_repo=cfg.neutts_codec,
                    codec_device="cpu",
                )
                _cache[key] = (tts, tts.encode_reference(cfg.neutts_ref_audio))
            model, codes = _cache[key]
            return model, codes

    def _run(self, text: str) -> bytes:
        import numpy as np

        model, codes = self._model()
        wav = model.infer(text, codes, i8_of(self.settings).tts.neutts_ref_text)
        samples = np.clip(np.asarray(wav, dtype="float32").reshape(-1), -1.0, 1.0)
        return wav_from_pcm((samples * 32767).astype("<i2").tobytes(), RATE)

    async def synthesize(self, text: str, *, voice: str | None = None, speed: float = 1.0) -> Audio:
        problem = self._problem()
        if problem is not None:
            raise VoiceUnavailable(f"tts/neutts: {problem[0]}; {problem[1]}")
        try:
            data = await asyncio.to_thread(self._run, text[: self.chunk_limit])
        except ImportError:
            raise VoiceUnavailable(f"tts/neutts: {INSTALL}") from None
        except VoiceUnavailable:
            raise
        except Exception as exc:
            raise AudioError(f"neutts failed: {type(exc).__name__}: {exc}"[:300]) from None
        return Audio(data, "audio/wav", RATE)
