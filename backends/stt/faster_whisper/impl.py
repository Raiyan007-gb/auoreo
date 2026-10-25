"""faster-whisper (CTranslate2) local STT. Imported lazily; the model is a process singleton.

Defaults per spec §26.16/§25.1: model `base`, CPU `int8` (Apple Silicon: `int8_float32`,
never float16), `beam_size=5`, `vad_filter=True` (500 ms min silence),
`condition_on_previous_text=False`, keyterms as `initial_prompt`. Weights are only fetched
from Hugging Face when `settings.voice_download_models` is true; otherwise the model must
already be in the HF cache (`check()` says which).
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import platform
import sys
import threading
from collections.abc import Sequence
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.stt import Segment, SttBackend, Transcript
from auoreo.voice.audio import AudioError, VoiceUnavailable

DEFAULT_MODEL = "base"
INSTALL = "uv pip install faster-whisper   (CTranslate2 wheels; CUDA optional)"
_lock = threading.Lock()
_models: dict[tuple[str, str, str], Any] = {}


def device_and_compute() -> tuple[str, str]:
    """Apple ARM pin `cpu` + `int8_float32`; everywhere else CPU `int8` (portable default)."""
    if sys.platform == "darwin" and platform.machine() == "arm64":
        return "cpu", "int8_float32"
    return "cpu", "int8"


class Backend(SttBackend):
    @property
    def model_name(self) -> str:
        return getattr(self.settings, "voice_stt_model", None) or DEFAULT_MODEL

    @property
    def download(self) -> bool:
        return bool(getattr(self.settings, "voice_download_models", False))

    async def check(self) -> BackendStatus:
        if importlib.util.find_spec("faster_whisper") is None:
            return BackendStatus(False, "faster-whisper is not installed", remedy=INSTALL)
        if self.download:
            return BackendStatus(True, f"model '{self.model_name}' (downloads on first use)")
        try:
            await asyncio.to_thread(self._cached_path)
        except Exception:
            return BackendStatus(
                False,
                f"model '{self.model_name}' is not downloaded",
                remedy='set "voice_download_models": true in settings.json (or pre-fetch the model)',
            )
        return BackendStatus(True, f"model '{self.model_name}' cached ({'/'.join(device_and_compute())})")

    def _cached_path(self) -> str:
        from faster_whisper.utils import download_model

        return str(download_model(self.model_name, local_files_only=True))

    def _model(self) -> Any:
        device, compute = device_and_compute()
        key = (self.model_name, device, compute)
        with _lock:
            if key not in _models:
                from faster_whisper import WhisperModel

                _models[key] = WhisperModel(
                    self.model_name, device=device, compute_type=compute, local_files_only=not self.download
                )
            return _models[key]

    def _run(self, wav: bytes, language: str | None, keyterms: Sequence[str]) -> Transcript:
        try:
            model = self._model()
        except ImportError:
            raise VoiceUnavailable(f"faster-whisper is not installed; {INSTALL}") from None
        except Exception as exc:
            raise VoiceUnavailable(
                f"cannot load whisper model '{self.model_name}' ({type(exc).__name__}); "
                'set "voice_download_models": true or pre-fetch it'
            ) from None
        try:
            segments, info = model.transcribe(
                io.BytesIO(wav),
                language=language,
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                condition_on_previous_text=False,
                initial_prompt=" ".join(keyterms)[:800] or None,
            )
            segs = [Segment(float(s.start), float(s.end), s.text.strip()) for s in segments]  # runs here
        except Exception as exc:
            raise AudioError(f"faster-whisper failed: {type(exc).__name__}: {exc}"[:300]) from None
        text = " ".join(s.text for s in segs if s.text).strip()
        return Transcript(text, segs, info.language, float(info.language_probability))

    async def transcribe(
        self, wav: bytes, *, language: str | None = None, keyterms: Sequence[str] = ()
    ) -> Transcript:
        return await asyncio.to_thread(self._run, wav, language, keyterms)
