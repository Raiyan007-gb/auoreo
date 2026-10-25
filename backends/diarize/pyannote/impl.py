"""pyannote.audio speaker diarization with `pyannote/speaker-diarization-community-1`.

Imported lazily (torch is heavy). The gated model needs a Hugging Face token from the
secret store (`HF_TOKEN`: env → keyring → `secrets.db`); it is passed to
`Pipeline.from_pretrained(..., token=...)` and never logged. The pipeline moves to CUDA when
available. The `exclusive_speaker_diarization` annotation (no overlapping turns) is used
when present, since meeting mode transcribes one turn at a time.
"""

from __future__ import annotations

import asyncio
import importlib.util
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.diarize import DiarizeBackend, Turn, relabel
from auoreo.voice.audio import VoiceUnavailable, read_wav
from auoreo.voice.cloud import secret

MODEL = "pyannote/speaker-diarization-community-1"
TOKEN = "HF_TOKEN"
INSTALL = "uv pip install pyannote.audio"
TOKEN_REMEDY = f"accept the {MODEL} conditions on huggingface.co, then `auoreo secrets set {TOKEN}`"


def _cuda() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


class Backend(DiarizeBackend):
    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._pipeline: Any = None

    async def check(self) -> BackendStatus:
        if importlib.util.find_spec("pyannote") is None or importlib.util.find_spec("pyannote.audio") is None:
            return BackendStatus(False, "pyannote.audio is not installed", remedy=INSTALL)
        if not secret(self.settings, TOKEN):
            return BackendStatus(False, f"{TOKEN} is not set ({MODEL} is gated)", remedy=TOKEN_REMEDY)
        return BackendStatus(True, f"{MODEL} (token {TOKEN} present; GPU recommended, loads on first use)")

    def _load(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        token = secret(self.settings, TOKEN)
        if not token:
            raise VoiceUnavailable(f"{TOKEN} is not set; {TOKEN_REMEDY}")
        try:
            from pyannote.audio import Pipeline
        except ImportError:
            raise VoiceUnavailable(f"pyannote.audio is not installed; {INSTALL}") from None
        pipeline = Pipeline.from_pretrained(MODEL, token=token)
        if pipeline is None:
            raise VoiceUnavailable(f"could not load {MODEL}; {TOKEN_REMEDY}")
        if _cuda():
            import torch

            pipeline.to(torch.device("cuda"))
        self._pipeline = pipeline
        return pipeline

    def _run(self, wav: bytes, num_speakers: int | None) -> list[Turn]:
        import numpy as np
        import torch

        pipeline = self._load()
        info = read_wav(wav)
        samples = np.frombuffer(info.pcm, dtype="<i2").astype("float32") / 32768.0
        audio = {"waveform": torch.from_numpy(samples).unsqueeze(0), "sample_rate": info.sample_rate}
        kwargs = {"num_speakers": num_speakers} if num_speakers else {}
        out = pipeline(audio, **kwargs)
        ann = getattr(out, "exclusive_speaker_diarization", None) or getattr(out, "speaker_diarization", out)
        return relabel([(seg.start, seg.end, spk) for seg, _, spk in ann.itertracks(yield_label=True)])

    async def diarize(self, wav16k_mono: bytes, *, num_speakers: int | None = None) -> list[Turn]:
        return await asyncio.to_thread(self._run, wav16k_mono, num_speakers)
