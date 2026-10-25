"""MediaPipe Tasks face detector (lazy import; IMAGE running mode; local-only).

`FaceDetector.create_from_options(FaceDetectorOptions(base_options=BaseOptions(
model_asset_path=…), running_mode=RunningMode.IMAGE))`, then `detector.detect(mp.Image(
image_format=mp.ImageFormat.SRGB, data=<HxWx3 uint8>))` → detections with `bounding_box`
(origin_x/origin_y/width/height), `categories[0].score` and normalised `keypoints`.
MediaPipe has no face embedding model, so `embeddings=True` yields `embedding=None`.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import httpx

from auoreo.backends import BackendStatus
from auoreo.backends.detection import Box, Face, FaceBackend
from auoreo.perception.blobs import decode
from auoreo.perception.models import DOWNLOAD_REMEDY, download_allowed, missing, models_dir, not_installed
from auoreo.tools.context import ToolError

MODEL_FILE = "blaze_face_short_range.tflite"
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_short_range/float16/latest/blaze_face_short_range.tflite"
)
INSTALL = "uv pip install mediapipe   (Apache-2.0)"
_lock = threading.Lock()
_detectors: dict[str, Any] = {}


class Backend(FaceBackend):
    name = "mediapipe"
    supports_embeddings = False

    @property
    def model_path(self) -> Path:
        return models_dir(self.settings) / MODEL_FILE

    async def check(self) -> BackendStatus:
        if mods := missing("mediapipe", "numpy"):
            return not_installed(mods, INSTALL)
        if self.model_path.is_file():
            return BackendStatus(True, f"BlazeFace short-range ({self.model_path}); local-only")
        if download_allowed(self.settings):
            return BackendStatus(True, "BlazeFace short-range (downloads on first use); local-only")
        return BackendStatus(False, f"face model not found at {self.model_path}", remedy=DOWNLOAD_REMEDY)

    def _ensure_model(self) -> Path:
        path = self.model_path
        if path.is_file():
            return path
        if not download_allowed(self.settings):
            raise ToolError(f"face model not found at {path}; {DOWNLOAD_REMEDY}")
        path.parent.mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            res = client.get(MODEL_URL)
            res.raise_for_status()
        tmp = path.with_suffix(".part")
        tmp.write_bytes(res.content)
        tmp.replace(path)
        return path

    def _detector(self) -> Any:
        with _lock:
            path = str(self._ensure_model())
            if path not in _detectors:
                import mediapipe as mp

                options = mp.tasks.vision.FaceDetectorOptions(
                    base_options=mp.tasks.BaseOptions(model_asset_path=path),
                    running_mode=mp.tasks.vision.RunningMode.IMAGE,
                )
                _detectors[path] = mp.tasks.vision.FaceDetector.create_from_options(options)
            return _detectors[path]

    def _run(self, image: bytes) -> list[Face]:
        try:
            detector = self._detector()
            import mediapipe as mp
            import numpy as np
        except ImportError:
            raise ToolError(f"mediapipe is not installed; {INSTALL}") from None
        img = decode(image)
        result = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.asarray(img)))
        out: list[Face] = []
        for det in result.detections:
            bb = det.bounding_box
            score = float(det.categories[0].score) if det.categories else 0.0
            box = Box(
                "face", score, bb.origin_x, bb.origin_y, bb.origin_x + bb.width, bb.origin_y + bb.height
            )
            marks = [(float(k.x) * img.width, float(k.y) * img.height) for k in det.keypoints or []]
            out.append(Face(box, marks, None))
        return out

    async def faces(self, image: bytes, *, embeddings: bool = False) -> list[Face]:
        return await asyncio.to_thread(self._run, image)
