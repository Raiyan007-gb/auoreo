"""RTSP camera probe (lazy OpenCV). Read-only: frames are pulled, nothing is sent to the
camera (no PTZ/ONVIF control without a separate, approved tool). The URL may carry
credentials, so every message here uses `redact_source`."""

from __future__ import annotations

from urllib.parse import urlsplit

from auoreo.perception.camera import OpenCVProbe
from auoreo.perception.sensors import redact_source
from auoreo.tools.context import ToolError

SCHEMES = {"rtsp", "rtsps"}


class Backend(OpenCVProbe):
    name = "rtsp"
    kind_label = "RTSP camera"

    def target(self, source: str) -> str:
        url = source.strip()
        try:
            parts = urlsplit(url)
        except ValueError:
            raise ToolError("rtsp source is not a valid URL") from None
        if parts.scheme.lower() not in SCHEMES or not parts.hostname:
            raise ToolError(f"rtsp source must be rtsp:// or rtsps:// with a host (got {redact_source(url)})")
        return url
