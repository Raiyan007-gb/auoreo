"""Desktop-control backend kind (`backends/desktop/<name>/`, M4d) behind the `computer` tool.

Coordinates passed to `act` are *relative*: `x`, `y` in 0–1000 across the primary display
(the backend maps them to pixels), so the model never needs the screen resolution. A
`target` that is not `x,y` is an element reference from the backend's last state read.

`delivery_mode="background"` (default) acts without raising or focusing the target window;
`"foreground"` may steal focus and is approved separately by the tool. Safety floors (lock /
logout combos, dangerous typed commands) live in the tool handler so every backend gets them.

I8 (spec §14.2/§24.6): ops `middle_click`, `set_value` (element token + value, AX set) and
`list_apps`; `act(..., opts=…)` carries `mode` (`som` overlays + elements | `vision` pixels |
`ax` tree text), sticky `window` `{pid, window_id}`, `value`, `capture_after` (re-read the
state after the action so element tokens are fresh), `settle_ms` (moveAndSettle) and
`no_overlay`. Element tokens returned in `ActResult.data["elements"]` are valid only for the
state they were read from (the tool refuses stale ones). Optional hooks with safe defaults:
`focused()` (input-target guard), `get_clipboard`/`set_clipboard` (typeViaClipboard),
`unhide()` (turn end) and `health_report()` (doctor; exit-code semantics 0 ok / 1 degraded /
2 unavailable, `display_count == 0` is unavailable).
"""

from __future__ import annotations

import io
import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from auoreo.backends import BackendStatus

Op = Literal[
    "click",
    "double_click",
    "right_click",
    "middle_click",
    "set_value",
    "type",
    "key",
    "scroll",
    "drag",
    "screenshot",
    "wait",
    "focus_app",
    "list_windows",
    "list_apps",
]
OPS: tuple[str, ...] = Op.__args__  # type: ignore[attr-defined]
MUTATING = frozenset(
    {
        "click",
        "double_click",
        "right_click",
        "middle_click",
        "set_value",
        "type",
        "key",
        "scroll",
        "drag",
        "focus_app",
    }
)
OBSERVE = frozenset({"screenshot", "list_windows", "list_apps"})
MODES = ("som", "vision", "ax")
VERDICTS = {"confirmed": "done", "unverifiable": "verify_fresh_state", "suspected_noop": "escalate"}
"""Verify verdict → the next step the model should take (spec §14.2 verify→escalate)."""
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


@dataclass(frozen=True)
class HealthReport:
    code: int
    """0 ready, 1 degraded (e.g. macOS permission missing), 2 unavailable."""
    detail: str
    display_count: int = 0
    accessibility: bool | None = None
    screen_recording: bool | None = None


def verdict_of(verify: Verify | None) -> tuple[str, str]:
    """`(verdict, next)`: no pre/post pair → unverifiable; changed → confirmed; else suspected_noop."""
    if verify is None or verify is UNVERIFIABLE:
        return "unverifiable", VERDICTS["unverifiable"]
    key = "confirmed" if verify.matched else "suspected_noop"
    return key, VERDICTS[key]


def no_overlay_default(platform: str | None = None, env: dict[str, str] | None = None) -> bool:
    """Spec §24.6: overlays off (`no_overlay`) on macOS / X11 / headless / WSL2; on (overlay
    allowed → False) on Windows and Wayland. Returns the `no_overlay` flag."""
    platform = platform or sys.platform
    env = dict(os.environ) if env is None else env
    if platform.startswith("win"):
        return False
    if platform == "darwin":
        return True
    if "microsoft" in env.get("WSL_DISTRO_NAME", "").lower() or env.get("WSL_DISTRO_NAME"):
        return True
    return not env.get("WAYLAND_DISPLAY")  # X11 / headless → no overlay


def downscale_png(png: bytes, max_dim: int) -> tuple[bytes, float]:
    """Cap the long edge at `max_dim` (spec: `max_image_dimension=1456`); returns (png, scale)."""
    from PIL import Image

    img: Image.Image = Image.open(io.BytesIO(png))
    long_edge = max(img.size)
    if long_edge <= max_dim:
        return png, 1.0
    scale = max_dim / long_edge
    img = img.resize(
        (max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.Resampling.LANCZOS
    )
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue(), scale


class DesktopBackend(ABC):
    supports_foreground: bool = True
    """False → the `computer` tool refuses `delivery_mode=foreground` (`foreground_unsupported`)."""

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
        opts: dict[str, Any] | None = None,
    ) -> ActResult:
        """Perform one op. Mutating ops return a post-action screenshot and `verify`
        (pre/post comparison); `screenshot` returns the current screen."""

    @abstractmethod
    async def state(self) -> DesktopState: ...

    async def focused(self) -> dict[str, Any] | None:
        """The focused window `{pid, window_id, app, title}`, or None when unknown."""
        st = await self.state()
        return next((w for w in st.windows if w.get("focused")), None)

    async def get_clipboard(self) -> str | None:
        """Clipboard text; None when the backend cannot read it (no typeViaClipboard)."""
        return None

    async def set_clipboard(self, text: str) -> bool:
        return False

    async def unhide(self) -> None:
        """Turn end: restore apps/overlays hidden during the turn (spec §14.2)."""
        return None

    async def health_report(self) -> HealthReport:
        status = await self.check()
        if not status.ok:
            return HealthReport(2, status.detail)
        try:
            count = (await self.state()).display_count
        except Exception as exc:
            return HealthReport(1, f"state unavailable: {exc}"[:200])
        if count == 0:
            return HealthReport(2, "display_count=0 (no display attached)", 0)
        return HealthReport(0, status.detail, count)

    async def close(self) -> None:
        return None


def to_pixels(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    """Relative 0–1000 coordinates → pixel coordinates on a `width`×`height` screen."""
    px = round(max(0, min(REL_MAX, x)) * (width - 1) / REL_MAX)
    py = round(max(0, min(REL_MAX, y)) * (height - 1) / REL_MAX)
    return px, py


UNVERIFIABLE = Verify(False, 0.0)
"""The verdict `compare` returns when there is no screenshot pair (identity-checked)."""


def compare(pre_png: bytes | None, post_png: bytes | None) -> Verify:
    """Changed-pixel fraction between two screenshots (downscaled to 256 px, greyscale).
    No pair to compare = unverifiable (`matched=False`, `score=0`)."""
    if not pre_png or not post_png:
        return UNVERIFIABLE
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
