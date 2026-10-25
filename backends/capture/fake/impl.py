"""Test capture backend: a generated 320x200 PNG with a timestamp (no display needed)."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from typing import Any

from PIL import Image, ImageDraw

from auoreo.backends import BackendStatus
from auoreo.backends.capture import CaptureBackend, Region
from auoreo.tools.context import ToolError

WIDTH, HEIGHT = 320, 200


class Backend(CaptureBackend):
    is_test_backend = True

    async def check(self) -> BackendStatus:
        return BackendStatus(True, "fake capture (test backend; generated image)")

    async def monitors(self) -> list[dict[str, Any]]:
        screen = {"left": 0, "top": 0, "width": WIDTH, "height": HEIGHT}
        return [{"index": 0, **screen}, {"index": 1, **screen}]

    async def grab(self, monitor: int | None = None, region: Region | None = None) -> bytes:
        if monitor is not None and monitor not in (0, 1):
            raise ToolError(f"monitor {monitor} does not exist (0..1)")
        img = Image.new("RGB", (WIDTH, HEIGHT), (32, 64, 128))
        draw = ImageDraw.Draw(img)
        draw.rectangle((10, 10, WIDTH - 10, HEIGHT - 10), outline=(255, 255, 255), width=2)
        draw.text((20, 20), "auoreo fake capture", fill=(255, 255, 255))
        draw.text((20, 40), datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), fill=(255, 220, 0))
        if region is not None:
            left, top, width, height = region
            if width <= 0 or height <= 0:
                raise ToolError("region width and height must be positive")
            img = img.crop((left, top, left + width, top + height))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
