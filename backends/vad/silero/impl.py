"""Silero VAD (ONNX). `load_silero_vad(onnx=True)` + `get_speech_timestamps(...,
return_seconds=True)` for whole files; per-frame `model(chunk, 16000)` probability (512-sample
windows at 16 kHz) for live endpointing. Imported lazily; the model is a singleton."""

from __future__ import annotations

import asyncio
import importlib.util
import threading
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.vad import VadBackend
from auoreo.voice.audio import VoiceUnavailable, read_wav

INSTALL = "uv pip install silero-vad onnxruntime"
THRESHOLD = 0.5
WINDOW = 512
_lock = threading.Lock()
_model: list[Any] = []


def _get() -> Any:
    with _lock:
        if not _model:
            try:
                from silero_vad import load_silero_vad
            except ImportError:
                raise VoiceUnavailable(f"silero-vad is not installed; {INSTALL}") from None
            _model.append(load_silero_vad(onnx=True))
        return _model[0]


def _tensor(pcm: bytes) -> Any:
    import numpy as np
    import torch

    x = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype="<i2").astype("float32") / 32768.0
    return torch.from_numpy(x)


class Backend(VadBackend):
    async def check(self) -> BackendStatus:
        missing = [m for m in ("silero_vad", "onnxruntime", "torch") if importlib.util.find_spec(m) is None]
        if missing:
            return BackendStatus(False, f"not installed: {', '.join(missing)}", remedy=INSTALL)
        return BackendStatus(True, "silero-vad (ONNX)")

    def _segments(self, wav: bytes) -> list[tuple[float, float]]:
        from silero_vad import get_speech_timestamps

        info = read_wav(wav)
        stamps = get_speech_timestamps(
            _tensor(info.pcm), _get(), sampling_rate=info.sample_rate, return_seconds=True
        )
        return [(float(s["start"]), float(s["end"])) for s in stamps]

    async def speech_segments(self, wav: bytes) -> list[tuple[float, float]]:
        return await asyncio.to_thread(self._segments, wav)

    def is_speech(self, frame_pcm16: bytes, sample_rate: int) -> bool:
        model = _get()
        x = _tensor(frame_pcm16)
        if x.numel() < WINDOW:
            return False
        probs = [
            float(model(x[i : i + WINDOW], sample_rate).item())
            for i in range(0, x.numel() - WINDOW + 1, WINDOW)
        ]
        return max(probs) > THRESHOLD
