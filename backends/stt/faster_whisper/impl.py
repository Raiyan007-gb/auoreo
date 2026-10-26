"""faster-whisper (CTranslate2) local STT. Imported lazily; the model is a process singleton.

Defaults per spec §26.16/§25.1: model `base`, CPU `int8` (Apple Silicon: `int8_float32`,
never float16), `beam_size=5`, `vad_filter=True` (500 ms min silence),
`condition_on_previous_text=False`, keyterms as `initial_prompt`. Weights are only fetched
from Hugging Face when `settings.voice_download_models` is true; otherwise the model must
already be in the HF cache (`check()` says which).

I8 (spec §13.2/§13.3):
- dual quality gate per segment: drop it only when `no_speech_prob > 0.6` AND
  `avg_logprob < -1.0` (`i8.stt.no_speech_threshold` / `logprob_threshold`, also passed to
  the decoder); when every segment is dropped the transcript is `no_speech=True` (re-listen);
- device `auto` (default) tries CUDA (`float16`, else the best supported compute type) when
  CTranslate2 sees a GPU and falls back to CPU `int8` when loading on CUDA fails;
  the compute type is checked against `ctranslate2.get_supported_compute_types(device)`;
- idle unload: the singleton is dropped `i8.stt.unload_after_idle_s` (300 s) after its last use.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import logging
import platform
import sys
import threading
import time
from collections.abc import Sequence
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.stt import Segment, SttBackend, Transcript
from auoreo.core.settings_i8 import i8_of
from auoreo.voice.audio import AudioError, VoiceUnavailable
from auoreo.voice.filters import gate_segment

log = logging.getLogger(__name__)

DEFAULT_MODEL = "base"
INSTALL = "uv pip install faster-whisper   (CTranslate2 wheels; CUDA optional)"
_lock = threading.Lock()
_models: dict[tuple[str, str, str], Any] = {}
_last_used: dict[tuple[str, str, str], float] = {}
_timers: dict[tuple[str, str, str], threading.Timer] = {}


def device_and_compute() -> tuple[str, str]:
    """Apple ARM pin `cpu` + `int8_float32`; everywhere else CPU `int8` (portable default)."""
    if sys.platform == "darwin" and platform.machine() == "arm64":
        return "cpu", "int8_float32"
    return "cpu", "int8"


def cuda_count() -> int:
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


def pick_compute(device: str, preferred: Sequence[str]) -> str:
    """First of `preferred` the device supports (exact names, spec §25.1)."""
    try:
        import ctranslate2

        supported = set(ctranslate2.get_supported_compute_types(device))
    except Exception:
        return preferred[0]
    return next((c for c in preferred if c in supported), preferred[-1])


def candidates(setting: str) -> list[tuple[str, str]]:
    """(device, compute_type) to try in order: CUDA first when allowed and present, then CPU."""
    cpu = device_and_compute()
    if sys.platform == "darwin" or setting == "cpu":
        return [cpu]
    if setting == "cuda" or cuda_count() > 0:
        return [("cuda", pick_compute("cuda", ("float16", "int8_float16", "int8"))), cpu]
    return [cpu]


def unload_all() -> int:
    with _lock:
        n = len(_models)
        _models.clear()
        _last_used.clear()
        for t in _timers.values():
            t.cancel()
        _timers.clear()
    return n


def _unload_if_idle(key: tuple[str, str, str], idle_s: float) -> None:
    with _lock:
        last = _last_used.get(key)
        if last is not None and time.monotonic() - last >= idle_s - 0.05:
            _models.pop(key, None)
            _last_used.pop(key, None)
            _timers.pop(key, None)
            log.info("faster-whisper: unloaded %s after %.0fs idle", key[0], idle_s)


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

    def _touch(self, key: tuple[str, str, str]) -> None:
        idle = float(i8_of(self.settings).stt.unload_after_idle_s)
        _last_used[key] = time.monotonic()
        if idle <= 0:
            return
        if (old := _timers.pop(key, None)) is not None:
            old.cancel()
        timer = threading.Timer(idle, _unload_if_idle, args=(key, idle))
        timer.daemon = True
        _timers[key] = timer
        timer.start()

    def _model(self) -> Any:
        errors: list[str] = []
        for device, compute in candidates(i8_of(self.settings).stt.device):
            key = (self.model_name, device, compute)
            with _lock:
                if key in _models:
                    self._touch(key)
                    return _models[key]
                from faster_whisper import WhisperModel

                try:
                    model = WhisperModel(
                        self.model_name,
                        device=device,
                        compute_type=compute,
                        local_files_only=not self.download,
                    )
                except ImportError:
                    raise
                except Exception as exc:
                    errors.append(f"{device}/{compute}: {type(exc).__name__}")
                    log.warning("faster-whisper: %s/%s failed (%s); falling back", device, compute, exc)
                    continue
                _models[key] = model
                self._touch(key)
                return model
        raise RuntimeError("; ".join(errors) or "no device")

    def _run(self, wav: bytes, language: str | None, keyterms: Sequence[str]) -> Transcript:
        cfg = i8_of(self.settings).stt
        try:
            model = self._model()
        except ImportError:
            raise VoiceUnavailable(f"faster-whisper is not installed; {INSTALL}") from None
        except Exception as exc:
            raise VoiceUnavailable(
                f"cannot load whisper model '{self.model_name}' ({exc}); "
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
                no_speech_threshold=cfg.no_speech_threshold,
                log_prob_threshold=cfg.logprob_threshold,
                initial_prompt=" ".join(keyterms)[:800] or None,
            )
            raw = list(segments)  # decoding runs here
        except Exception as exc:
            raise AudioError(f"faster-whisper failed: {type(exc).__name__}: {exc}"[:300]) from None
        kept = [
            s
            for s in raw
            if gate_segment(getattr(s, "no_speech_prob", None), getattr(s, "avg_logprob", None), cfg)
        ]
        segs = [Segment(float(s.start), float(s.end), s.text.strip()) for s in kept]
        text = " ".join(s.text for s in segs if s.text).strip()
        no_speech = bool(raw) and not kept
        return Transcript(text, segs, info.language, float(info.language_probability), no_speech=no_speech)

    async def transcribe(
        self, wav: bytes, *, language: str | None = None, keyterms: Sequence[str] = ()
    ) -> Transcript:
        return await asyncio.to_thread(self._run, wav, language, keyterms)
