"""sherpa-onnx keyword spotter (Apache-2.0). Imported lazily.

The model folder (`settings.voice_kws_model_dir`, e.g. `kws-zh-en-3M-2025-12-20`) must hold
`tokens.txt`, `encoder*.onnx`, `decoder*.onnx`, `joiner*.onnx` and a `keywords.txt` whose
lines are generated with `sherpa-onnx-cli text2token` (`▁HE Y ▁O RE O :1.5 #0.35`).
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.wake import WakeBackend, WakeHit
from auoreo.voice.audio import VoiceUnavailable, read_wav

INSTALL = "uv pip install sherpa-onnx"
MODEL_REMEDY = (
    "download a KWS model (https://k2-fsa.github.io/sherpa/onnx/kws/pretrained_models/) and set "
    '"voice_kws_model_dir" in settings.json; generate keywords.txt with `sherpa-onnx-cli text2token`'
)


def _pick(folder: Path, prefix: str) -> Path | None:
    files = sorted(folder.glob(f"{prefix}*.onnx"), key=lambda p: (".int8." not in p.name, p.name))
    return files[0] if files else None


def model_files(folder: Path | None) -> dict[str, Path] | str:
    """Paths of the KWS files, or a string naming what is missing."""
    if folder is None:
        return "voice_kws_model_dir is not set"
    found: dict[str, Path | None] = {
        "tokens": folder / "tokens.txt",
        "encoder": _pick(folder, "encoder"),
        "decoder": _pick(folder, "decoder"),
        "joiner": _pick(folder, "joiner"),
        "keywords_file": folder / "keywords.txt",
    }
    missing = [k for k, p in found.items() if p is None or not p.is_file()]
    if missing:
        return f"missing in {folder}: {', '.join(missing)}"
    return {k: p for k, p in found.items() if p is not None}


class Backend(WakeBackend):
    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._kws: Any = None

    async def check(self) -> BackendStatus:
        if importlib.util.find_spec("sherpa_onnx") is None:
            return BackendStatus(False, "sherpa-onnx is not installed", remedy=INSTALL)
        files = model_files(getattr(self.settings, "voice_kws_model_dir", None))
        if isinstance(files, str):
            return BackendStatus(False, files, remedy=MODEL_REMEDY)
        return BackendStatus(True, f"KWS model in {files['tokens'].parent}")

    def _spotter(self) -> Any:
        if self._kws is None:
            files = model_files(getattr(self.settings, "voice_kws_model_dir", None))
            if isinstance(files, str):
                raise VoiceUnavailable(f"wake/sherpa_onnx: {files}; {MODEL_REMEDY}")
            try:
                import sherpa_onnx
            except ImportError:
                raise VoiceUnavailable(f"sherpa-onnx is not installed; {INSTALL}") from None
            self._kws = sherpa_onnx.KeywordSpotter(
                **{k: str(v) for k, v in files.items()}, num_threads=1, provider="cpu"
            )
        return self._kws

    def _detect(self, wav: bytes) -> WakeHit | None:
        import numpy as np

        kws = self._spotter()
        info = read_wav(wav)
        samples = np.frombuffer(info.pcm, dtype="<i2").astype("float32") / 32768.0
        stream = kws.create_stream()
        stream.accept_waveform(info.sample_rate, samples)
        stream.accept_waveform(info.sample_rate, np.zeros(int(0.66 * info.sample_rate), dtype="float32"))
        stream.input_finished()
        while kws.is_ready(stream):
            kws.decode_stream(stream)
            hit = kws.get_result(stream)
            if hit:
                kws.reset_stream(stream)
                return WakeHit(hit, 0.0, 1.0)
        return None

    async def detect(self, wav: bytes) -> WakeHit | None:
        return await asyncio.to_thread(self._detect, wav)
