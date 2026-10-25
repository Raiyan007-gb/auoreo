"""Grounding-DINO tiny via transformers (lazy import; processor+model are a singleton).

`AutoProcessor` / `AutoModelForZeroShotObjectDetection`, then
`processor.post_process_grounded_object_detection(outputs, input_ids, threshold=…,
text_threshold=…, target_sizes=[(h, w)])`. Without `perception_download_models` the model
is loaded with `local_files_only=True` (it must already be in the HF cache).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Sequence
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.detection import Box, DetectionBackend
from auoreo.perception.blobs import decode
from auoreo.perception.models import DOWNLOAD_REMEDY, download_allowed, missing, not_installed
from auoreo.tools.context import ToolError

MODEL_ID = "IDEA-Research/grounding-dino-tiny"
INSTALL = "uv pip install transformers torch   (Apache-2.0 weights)"
DEFAULT_CLASSES = ("person", "object")
TEXT_THRESHOLD = 0.25
_lock = threading.Lock()
_loaded: dict[str, tuple[Any, Any]] = {}


class Backend(DetectionBackend):
    name = "grounding_dino"
    open_vocabulary = True

    async def check(self) -> BackendStatus:
        if mods := missing("transformers", "torch"):
            return not_installed(mods, INSTALL)
        if download_allowed(self.settings):
            return BackendStatus(True, f"{MODEL_ID} (downloads on first use)")
        try:
            await asyncio.to_thread(self._cached)
        except Exception:
            return BackendStatus(
                False, f"{MODEL_ID} is not in the Hugging Face cache", remedy=DOWNLOAD_REMEDY
            )
        return BackendStatus(True, f"{MODEL_ID} cached")

    def _cached(self) -> str:
        from huggingface_hub import snapshot_download

        return str(snapshot_download(MODEL_ID, local_files_only=True))

    def _load(self) -> tuple[Any, Any]:
        with _lock:
            if MODEL_ID not in _loaded:
                from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

                local = not download_allowed(self.settings)
                processor = AutoProcessor.from_pretrained(MODEL_ID, local_files_only=local)
                model = AutoModelForZeroShotObjectDetection.from_pretrained(MODEL_ID, local_files_only=local)
                _loaded[MODEL_ID] = (processor, model.eval())
            return _loaded[MODEL_ID]

    def _run(self, image: bytes, classes: Sequence[str], threshold: float) -> list[Box]:
        try:
            processor, model = self._load()
            import torch
        except ImportError:
            raise ToolError(f"transformers/torch are not installed; {INSTALL}") from None
        except OSError:
            raise ToolError(f"{MODEL_ID} is not cached; {DOWNLOAD_REMEDY}") from None
        img = decode(image)
        labels = [c.strip() for c in classes if c.strip()] or list(DEFAULT_CLASSES)
        inputs = processor(images=img, text=[labels], return_tensors="pt")
        with torch.no_grad():
            outputs = model(**inputs)
        result = processor.post_process_grounded_object_detection(
            outputs,
            inputs.input_ids,
            threshold=threshold,
            text_threshold=TEXT_THRESHOLD,
            target_sizes=[(img.height, img.width)],
        )[0]
        names = result.get("text_labels", result.get("labels", []))
        out: list[Box] = []
        for box, score, label in zip(result["boxes"], result["scores"], names, strict=False):
            x0, y0, x1, y1 = (float(v) for v in box.tolist())
            out.append(Box(str(label), float(score), x0, y0, x1, y1))
        return out

    async def detect(self, image: bytes, *, classes: Sequence[str] = (), threshold: float = 0.3) -> list[Box]:
        return await asyncio.to_thread(self._run, image, classes, threshold)
