"""USB/UVC camera probe: `source` is a device index (lazy OpenCV, read-only)."""

from __future__ import annotations

from auoreo.perception.camera import OpenCVProbe
from auoreo.tools.context import ToolError

MAX_INDEX = 63


class Backend(OpenCVProbe):
    name = "uvc"
    kind_label = "UVC camera"

    def target(self, source: str) -> int:
        raw = source.strip().removeprefix("uvc:").removeprefix("/dev/video")
        if not raw.isdigit() or int(raw) > MAX_INDEX:
            raise ToolError(f'uvc source must be a device index 0..{MAX_INDEX} (e.g. "0")')
        return int(raw)
