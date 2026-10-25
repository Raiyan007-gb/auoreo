"""Shared OpenCV camera probe for the `uvc` and `rtsp` sensor backends (lazy `cv2`).

`cv2.VideoCapture(index | url)`, `read()` one frame per sample, JPEG-encode it with Pillow.
`presence` is "the scene changed": mean absolute difference of a 64 px grayscale thumbnail
against the previous frame above `MOTION`; `reading` carries the frame size, the motion
score and a frame counter. Read-only: probes never send camera control (no PTZ/ONVIF).
"""

from __future__ import annotations

import asyncio
import importlib
import io
from typing import Any

from PIL import Image, ImageChops, ImageStat

from auoreo.backends import BackendStatus
from auoreo.backends.sensor import Sample, SensorProbe
from auoreo.perception.models import missing, not_installed
from auoreo.tools.context import ToolError

INSTALL = "uv pip install opencv-python-headless   (Apache-2.0)"
MOTION = 6.0
THUMB = (64, 48)


def _cv2() -> Any:
    return importlib.import_module("cv2")  # lazy; optional dependency without type stubs


def motion_score(prev: Image.Image | None, cur: Image.Image) -> float:
    if prev is None:
        return 0.0
    return float(ImageStat.Stat(ImageChops.difference(prev, cur)).mean[0])


def to_jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


class OpenCVProbe(SensorProbe):
    """Subclasses implement `target(source)` → what `cv2.VideoCapture` opens."""

    kind_label = "camera"

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._cap: Any = None
        self._prev: Image.Image | None = None
        self._frames = 0

    def capabilities(self) -> dict[str, bool]:
        return {"video": True, "audio": False, "telemetry": False, "ptz": False}

    async def check(self) -> BackendStatus:
        if mods := missing("cv2"):
            return not_installed(mods, INSTALL)
        return BackendStatus(True, f"OpenCV {self.kind_label} probe (read-only)")

    def target(self, source: str) -> int | str:
        raise NotImplementedError

    def _open(self, target: int | str) -> Any:
        try:
            cv2 = _cv2()
        except ImportError:
            raise ToolError(f"opencv is not installed; {INSTALL}") from None
        cap = cv2.VideoCapture(target)
        if not cap.isOpened():
            cap.release()
            raise ToolError(f"cannot open {self.kind_label} source")
        return cap

    async def start(self, source: str) -> None:
        target = self.target(source)
        self._cap = await asyncio.to_thread(self._open, target)

    def _grab(self) -> Sample:
        cv2 = _cv2()
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return Sample(False, {"error": "no frame"}, 0.0, None)
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        thumb = img.convert("L").resize(THUMB)
        score = motion_score(self._prev, thumb)
        self._prev = thumb
        self._frames += 1
        reading = {"width": img.width, "height": img.height, "motion": round(score, 2), "frame": self._frames}
        return Sample(score > MOTION, reading, 1.0 if self._frames > 1 else 0.5, to_jpeg(img))

    async def sample(self) -> Sample:
        if self._cap is None:
            raise ToolError(f"{self.kind_label} probe is not started")
        return await asyncio.to_thread(self._grab)

    async def stop(self) -> None:
        cap, self._cap = self._cap, None
        if cap is not None:
            await asyncio.to_thread(cap.release)
