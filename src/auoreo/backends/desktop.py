"""Desktop-control backend kind (`backends/desktop/<name>/`, M4d) behind the `computer` tool.

Coordinates passed to `act` are *relative*: `x`, `y` in 0–1000 across the primary display
(the backend maps them to pixels), so the model never needs the screen resolution. A
`target` that is not `x,y` is an element reference from the backend's last state read.

`delivery_mode="background"` (default) acts without raising or focusing the target window;
`"foreground"` may steal focus and is approved separately by the tool. Safety floors (lock /
logout combos, dangerous typed commands) live in the tool handler so every backend gets them.
"""

from __future__ import annotations

import io
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from auoreo.backends import BackendStatus

Op = Literal[
    "click",
    "double_click",
    "right_click",
    "type",
    "key",
    "scroll",
    "drag",
    "screenshot",
    "wait",
    "focus_app",
    "list_windows",
]
OPS: tuple[str, ...] = Op.__args__  # type: ignore[attr-defined]
MUTATING = frozenset({"click", "double_click", "right_click", "type", "key", "scroll", "drag", "focus_app"})
DeliveryMode = Literal["background", "foreground"]
REL_MAX = 1000
VERIFY_THRESHOLD = 0.002
"""Fraction of changed pixels above which a pre/post screenshot pair counts as `matched`."""


@dataclass(frozen=True)
class Verify:
    matched: bool
    score: float


@dataclass
class ActResult:
    ok: bool
    screenshot_png: bytes | None = None
    verify: Verify | None = None
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class DesktopState:
    apps: list[str] = field(default_factory=list)
    windows: list[dict[str, Any]] = field(default_factory=list)
    """`{id, app, title, focused}` per top-level window."""
    display_count: int = 0


class DesktopBackend(ABC):
    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @abstractmethod
    async def check(self) -> BackendStatus: ...

    @abstractmethod
    async def act(
        self,
        op: str,
        *,
        target: str | None = None,
        x: int | None = None,
        y: int | None = None,
        text: str | None = None,
        key: str | None = None,
        delivery_mode: str = "background",
        to: tuple[int, int] | None = None,
        direction: str | None = None,
        amount: int | None = None,
    ) -> ActResult:
        """Perform one op. Mutating ops return a post-action screenshot and `verify`
        (pre/post comparison); `screenshot` returns the current screen."""

    @abstractmethod
    async def state(self) -> DesktopState: ...

    async def close(self) -> None:
        return None


def to_pixels(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    """Relative 0–1000 coordinates → pixel coordinates on a `width`×`height` screen."""
    px = round(max(0, min(REL_MAX, x)) * (width - 1) / REL_MAX)
    py = round(max(0, min(REL_MAX, y)) * (height - 1) / REL_MAX)
    return px, py


def compare(pre_png: bytes | None, post_png: bytes | None) -> Verify:
    """Changed-pixel fraction between two screenshots (downscaled to 256 px, greyscale).
    No pair to compare = unverifiable (`matched=False`, `score=0`)."""
    if not pre_png or not post_png:
        return Verify(False, 0.0)
    from PIL import Image, ImageChops

    def load(data: bytes) -> Image.Image:
        img = Image.open(io.BytesIO(data)).convert("L")
        img.thumbnail((256, 256))
        return img

    a, b = load(pre_png), load(post_png)
    if a.size != b.size:
        return Verify(True, 1.0)
    diff = ImageChops.difference(a, b).point(lambda v: 255 if v > 16 else 0)
    hist = diff.histogram()
    changed = hist[255] / max(1, a.width * a.height)
    return Verify(changed > VERIFY_THRESHOLD, round(changed, 4))
