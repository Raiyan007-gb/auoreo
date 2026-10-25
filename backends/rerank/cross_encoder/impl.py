"""Local cross-encoder reranker (sentence-transformers `CrossEncoder.predict`).

Default model `cross-encoder/ms-marco-MiniLM-L6-v2` (English, ~22M params, ~200 ms for 30
pairs on CPU). `BAAI/bge-reranker-v2-m3` (multilingual, ~568M params, ~2 s on CPU) and others via
`settings.rerank_model`: the sequence-classification conversion of `Qwen/Qwen3-Reranker-0.6B`
(`tomaarsen/Qwen3-Reranker-0.6B-seq-cls`; the original checkpoint is a causal LM that
CrossEncoder cannot score), or small English models such as
`cross-encoder/ms-marco-MiniLM-L6-v2`. ColBERT / Jina late-interaction rerankers (spec §25)
are out of scope for this backend.

sentence-transformers is imported lazily (`uv sync --extra rerank-local`). Weights are
fetched from Hugging Face only when `settings.rerank_download_models` is true; otherwise
the model must already be in the HF cache and `check()` says so. The model is a process
singleton; the first call may spend its timeout budget loading it (the load finishes in
the background and later queries use it).
"""

from __future__ import annotations

import asyncio
import importlib.util
import threading
from collections.abc import Sequence
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.rerank import Ranked, RerankBackend

DEFAULT_MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"
INSTALL = "uv sync --extra rerank-local   (sentence-transformers + CPU torch)"
MAX_LENGTH = 512
WEIGHTS = ("model.safetensors", "pytorch_model.bin")
_lock = threading.Lock()
_busy = threading.Lock()
"""Single flight: a timed-out inference keeps running in its thread; later queries fail open
instead of piling more CPU-bound work behind it."""
_models: dict[tuple[str, bool], Any] = {}


class Backend(RerankBackend):
    name = "cross_encoder"

    @property
    def model_name(self) -> str:
        return getattr(self.settings, "rerank_model", None) or DEFAULT_MODEL

    @property
    def download(self) -> bool:
        return bool(getattr(self.settings, "rerank_download_models", False))

    @property
    def timeout(self) -> float:
        return float(getattr(self.settings, "rerank_timeout_s", 2.0))

    async def check(self) -> BackendStatus:
        if importlib.util.find_spec("sentence_transformers") is None:
            return BackendStatus(False, "sentence-transformers is not installed", remedy=INSTALL)
        if self.download:
            return BackendStatus(True, f"model '{self.model_name}' (downloads on first use)")
        try:
            await asyncio.to_thread(self._cached_path)
        except Exception:
            return BackendStatus(
                False,
                f"model '{self.model_name}' is not downloaded",
                remedy='set "rerank_download_models": true in settings.json (or pre-fetch the model)',
            )
        return BackendStatus(True, f"model '{self.model_name}' cached (CPU)")

    def _cached_path(self) -> str:
        """Path of the cached config; raises when config or weights are not in the HF cache."""
        from huggingface_hub import try_to_load_from_cache

        config = try_to_load_from_cache(self.model_name, "config.json")
        weights = [try_to_load_from_cache(self.model_name, f) for f in WEIGHTS]
        if not isinstance(config, str) or not any(isinstance(w, str) for w in weights):
            raise FileNotFoundError(self.model_name)
        return config

    def _model(self) -> Any:
        key = (self.model_name, self.download)
        with _lock:
            if key not in _models:
                from sentence_transformers import CrossEncoder

                _models[key] = CrossEncoder(
                    self.model_name, local_files_only=not self.download, max_length=MAX_LENGTH
                )
            return _models[key]

    async def warm(self) -> None:
        """Load the weights now (eval script, optional server warm-up)."""
        await asyncio.to_thread(self._model)

    def _score(self, query: str, documents: Sequence[str]) -> list[float]:
        if not _busy.acquire(blocking=False):
            raise RuntimeError("cross-encoder is still busy with an earlier (timed-out) query")
        try:
            scores = self._model().predict([(query, d) for d in documents], show_progress_bar=False)
        finally:
            _busy.release()
        return [float(s) for s in scores]

    async def rerank(self, query: str, documents: Sequence[str], top_n: int) -> list[Ranked]:
        if not documents:
            return []
        scores = await asyncio.wait_for(asyncio.to_thread(self._score, query, documents), self.timeout)
        ranked = sorted((Ranked(i, s) for i, s in enumerate(scores)), key=lambda r: (-r.score, r.index))
        return ranked[:top_n]
