"""RF-DETR detector (lazy `rfdetr`; model per size is a process singleton).

`model.predict(PIL image, threshold)` returns a `supervision.Detections` (`xyxy`,
`confidence`, `class_id`); labels come from `rfdetr.util.coco_classes.COCO_CLASSES`. A bare
weights filename makes rfdetr download into its cache (`RF_HOME`, default
~/.roboflow/models), so without `perception_download_models` the cached file is passed as
an explicit path (used as-is, never downloaded) and `check()` fails when it is absent.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Sequence
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.detection import Box, DetectionBackend
from auoreo.perception.blobs import decode
from auoreo.perception.models import (
    DOWNLOAD_REMEDY,
    ModelRefused,
    download_allowed,
    missing,
    not_installed,
    rfdetr_cache,
    rfdetr_size,
)
from auoreo.tools.context import ToolError

INSTALL = "uv pip install rfdetr   (pulls torch; Apache-2.0)"
_lock = threading.Lock()
_models: dict[str, Any] = {}


class Backend(DetectionBackend):
    name = "rfdetr"

    async def check(self) -> BackendStatus:
        try:
            size, _cls, weights = rfdetr_size(self.settings)
        except ModelRefused as exc:
            return BackendStatus(False, str(exc), remedy='set "detection_model": "nano"')
        if mods := missing("rfdetr"):
            return not_installed(mods, INSTALL)
        if download_allowed(self.settings):
            return BackendStatus(True, f"RF-DETR {size} (weights download on first use)")
        path = rfdetr_cache() / weights
        if not path.is_file():
            return BackendStatus(
                False, f"RF-DETR {size} weights not cached at {path}", remedy=DOWNLOAD_REMEDY
            )
        return BackendStatus(True, f"RF-DETR {size} cached ({path})")

    def _model(self) -> Any:
        size, cls_name, weights = rfdetr_size(self.settings)
        with _lock:
            if size not in _models:
                import rfdetr

                cached = rfdetr_cache() / weights
                if not download_allowed(self.settings) and not cached.is_file():
                    raise ToolError(f"RF-DETR {size} weights are not cached; {DOWNLOAD_REMEDY}")
                ref = weights if download_allowed(self.settings) else str(cached)
                _models[size] = getattr(rfdetr, cls_name)(pretrain_weights=ref)
            return _models[size]

    def _run(self, image: bytes, classes: Sequence[str], threshold: float) -> list[Box]:
        try:
            model = self._model()
            from rfdetr.util.coco_classes import COCO_CLASSES
        except ModelRefused as exc:
            raise ToolError(str(exc)) from None
        except ImportError:
            raise ToolError(f"rfdetr is not installed; {INSTALL}") from None
        dets = model.predict(decode(image), threshold=threshold)
        wanted = {c.strip().lower() for c in classes if c.strip()}
        out: list[Box] = []
        for xyxy, score, cid in zip(dets.xyxy, dets.confidence, dets.class_id, strict=False):
            label = str(COCO_CLASSES.get(int(cid), f"class_{int(cid)}"))
            if wanted and label.lower() not in wanted:
                continue
            x0, y0, x1, y1 = (float(v) for v in xyxy)
            out.append(Box(label, float(score), x0, y0, x1, y1))
        return out

    async def detect(self, image: bytes, *, classes: Sequence[str] = (), threshold: float = 0.3) -> list[Box]:
        return await asyncio.to_thread(self._run, image, classes, threshold)
