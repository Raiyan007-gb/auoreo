"""mss screen capture. `mss` is imported lazily so discovery never needs a display."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.capture import CaptureBackend, Region
from auoreo.tools.context import ToolError

REMEDY = (
    "run auoreo inside a graphical session (DISPLAY or WAYLAND_DISPLAY set on Linux), "
    'or select the test backend with settings.backends.capture = "fake"'
)


def _session() -> Any:
    import mss

    factory = getattr(mss, "MSS", None) or mss.mss
    return factory()


def _no_display() -> bool:
    return sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )


def _monitors() -> list[dict[str, Any]]:
    with _session() as sct:
        return [
            {"index": i, "left": m["left"], "top": m["top"], "width": m["width"], "height": m["height"]}
            for i, m in enumerate(sct.monitors)
        ]


def _grab(monitor: int | None, region: Region | None) -> bytes:
    import mss.tools

    with _session() as sct:
        if region is not None:
            left, top, width, height = region
            box: Any = {"left": left, "top": top, "width": width, "height": height}
        else:
            idx = 1 if monitor is None else monitor
            if not 0 <= idx < len(sct.monitors):
                raise ToolError(f"monitor {idx} does not exist (0..{len(sct.monitors) - 1})")
            box = sct.monitors[idx]
        shot = sct.grab(box)
        png = mss.tools.to_png(shot.rgb, shot.size)
        assert png is not None
        return bytes(png)


class Backend(CaptureBackend):
    async def check(self) -> BackendStatus:
        if _no_display():
            return BackendStatus(False, "no display", remedy=REMEDY)
        try:
            mons = await asyncio.to_thread(_monitors)
        except ImportError:
            return BackendStatus(False, "mss is not installed", remedy="uv add mss")
        except Exception as exc:  # ScreenShotError, XOpenDisplay failures, …
            return BackendStatus(False, f"no display ({type(exc).__name__}: {exc})"[:300], remedy=REMEDY)
        return BackendStatus(True, f"{max(0, len(mons) - 1)} monitor(s)")

    async def monitors(self) -> list[dict[str, Any]]:
        self._require_display()
        return await asyncio.to_thread(_monitors)

    async def grab(self, monitor: int | None = None, region: Region | None = None) -> bytes:
        self._require_display()
        try:
            return await asyncio.to_thread(_grab, monitor, region)
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"screen capture failed: {type(exc).__name__}: {exc}; {REMEDY}") from exc

    @staticmethod
    def _require_display() -> None:
        if _no_display():
            raise ToolError(f"no display available for screen capture; {REMEDY}")
