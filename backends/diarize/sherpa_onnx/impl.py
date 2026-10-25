"""sherpa-onnx offline speaker diarization (Apache-2.0), the local default (spec §22-5).

Imported lazily. Pipeline (sherpa-onnx `OfflineSpeakerDiarization`): pyannote
segmentation-3.0 (ONNX) → speaker embeddings (3D-Speaker ERes2Net ONNX by default) → fast
clustering (`num_clusters` = `voice_diarize_num_speakers`, else threshold
`voice_diarize_threshold`; larger threshold → fewer speakers).

Models: `voice_diarize_segmentation_model` (a `model.onnx`) and
`voice_diarize_embedding_model` (an `.onnx`). When unset and `voice_download_models` is
true, the release assets below are downloaded into `<home>/models/diarize/` on first use;
downloading never happens otherwise.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import tarfile
from pathlib import Path
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.diarize import DiarizeBackend, Turn, relabel
from auoreo.voice.audio import VoiceUnavailable, read_wav

INSTALL = "uv pip install sherpa-onnx"
RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
SEGMENTATION_URL = f"{RELEASES}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
EMBEDDING_NAME = "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
EMBEDDING_URL = f"{RELEASES}/speaker-recongition-models/{EMBEDDING_NAME}"  # sic: upstream tag
MODEL_REMEDY = (
    'set "voice_diarize_segmentation_model" and "voice_diarize_embedding_model" in settings.json '
    "(https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/models.html), "
    'or set "voice_download_models": true'
)


def _cache(settings: Any) -> Path:
    return Path(settings.home) / "models" / "diarize"


def model_paths(settings: Any) -> tuple[Path, Path] | str:
    """`(segmentation, embedding)` paths, or a string naming what is missing."""
    seg = getattr(settings, "voice_diarize_segmentation_model", None)
    emb = getattr(settings, "voice_diarize_embedding_model", None)
    cache = _cache(settings)
    seg = Path(seg) if seg else cache / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
    emb = Path(emb) if emb else cache / EMBEDDING_NAME
    missing = [str(p) for p in (seg, emb) if not p.is_file()]
    return f"missing model file(s): {', '.join(missing)}" if missing else (seg, emb)


def download(settings: Any) -> None:
    """Fetch the default models into the cache (only called with `voice_download_models`)."""
    import httpx

    cache = _cache(settings)
    cache.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        if not (cache / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx").is_file():
            r = client.get(SEGMENTATION_URL)
            r.raise_for_status()
            with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:bz2") as tar:
                tar.extractall(cache, filter="data")
        if not (cache / EMBEDDING_NAME).is_file():
            r = client.get(EMBEDDING_URL)
            r.raise_for_status()
            (cache / EMBEDDING_NAME).write_bytes(r.content)


class Backend(DiarizeBackend):
    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._sd: Any = None

    async def check(self) -> BackendStatus:
        if importlib.util.find_spec("sherpa_onnx") is None:
            return BackendStatus(False, "sherpa-onnx is not installed", remedy=INSTALL)
        paths = model_paths(self.settings)
        if isinstance(paths, str):
            if getattr(self.settings, "voice_download_models", False):
                return BackendStatus(True, f"{paths}; will download on first use (voice_download_models)")
            return BackendStatus(False, paths, remedy=MODEL_REMEDY)
        return BackendStatus(True, f"segmentation {paths[0].name}, embedding {paths[1].name}")

    def _diarizer(self, num_speakers: int | None) -> Any:
        paths = model_paths(self.settings)
        if isinstance(paths, str) and getattr(self.settings, "voice_download_models", False):
            download(self.settings)
            paths = model_paths(self.settings)
        if isinstance(paths, str):
            raise VoiceUnavailable(f"diarize/sherpa_onnx: {paths}; {MODEL_REMEDY}")
        try:
            import sherpa_onnx
        except ImportError:
            raise VoiceUnavailable(f"sherpa-onnx is not installed; {INSTALL}") from None
        clusters = num_speakers or getattr(self.settings, "voice_diarize_num_speakers", None) or -1
        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(paths[0])),
            ),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(paths[1])),
            clustering=sherpa_onnx.FastClusteringConfig(
                num_clusters=clusters, threshold=float(getattr(self.settings, "voice_diarize_threshold", 0.5))
            ),
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
        if not config.validate():
            raise VoiceUnavailable(f"diarize/sherpa_onnx: invalid model config; {MODEL_REMEDY}")
        return sherpa_onnx.OfflineSpeakerDiarization(config)

    def _run(self, wav: bytes, num_speakers: int | None) -> list[Turn]:
        import numpy as np

        if self._sd is None or num_speakers:
            self._sd = self._diarizer(num_speakers)
        info = read_wav(wav)
        if info.sample_rate != self._sd.sample_rate:
            raise VoiceUnavailable(f"diarize/sherpa_onnx expects {self._sd.sample_rate} Hz audio")
        samples = np.frombuffer(info.pcm, dtype="<i2").astype("float32") / 32768.0
        result = self._sd.process(samples).sort_by_start_time()
        return relabel([(r.start, r.end, r.speaker) for r in result])

    async def diarize(self, wav16k_mono: bytes, *, num_speakers: int | None = None) -> list[Turn]:
        return await asyncio.to_thread(self._run, wav16k_mono, num_speakers)
