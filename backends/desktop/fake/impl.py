"""In-memory fake desktop: a window list, Pillow-drawn screenshots, recorded actions.

Each screenshot draws the windows (focused one highlighted), a cursor at the last pointer
position and the last typed text, so a mutating op changes the picture and the generic
pre/post `compare` verifies it. Tests may script outcomes: `backend.scripted_verify` (a
list of `Verify`, consumed in order) overrides the computed verdict; `backend.fail_ops`
makes those ops report `ok=False`.
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import asdict
from typing import Any

from PIL import Image, ImageDraw

from auoreo.backends import BackendStatus
from auoreo.backends.desktop import (
    MUTATING,
    ActResult,
    DesktopBackend,
    DesktopState,
    Verify,
    compare,
    to_pixels,
)
from auoreo.tools.context import ToolError

WIDTH, HEIGHT = 640, 400


class Backend(DesktopBackend):
    is_test_backend = True

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self.windows: list[dict[str, Any]] = [
            {"id": 1, "app": "Finder", "title": "Home", "focused": True},
            {"id": 2, "app": "TextEdit", "title": "Untitled", "focused": False},
        ]
        self.actions: list[dict[str, Any]] = []
        self.cursor: tuple[int, int] = (WIDTH // 2, HEIGHT // 2)
        self.typed = ""
        self.scripted_verify: list[Verify] = []
        self.fail_ops: set[str] = set()

    async def check(self) -> BackendStatus:
        return BackendStatus(True, "fake desktop (test backend; in-memory windows)")

    async def state(self) -> DesktopState:
        apps = sorted({w["app"] for w in self.windows})
        return DesktopState(apps=apps, windows=[dict(w) for w in self.windows], display_count=1)

    def screenshot(self) -> bytes:
        img = Image.new("RGB", (WIDTH, HEIGHT), (40, 44, 52))
        draw = ImageDraw.Draw(img)
        for i, w in enumerate(self.windows):
            x0, y0 = 30 + i * 60, 30 + i * 50
            colour = (90, 140, 230) if w["focused"] else (120, 120, 120)
            draw.rectangle((x0, y0, x0 + 360, y0 + 220), fill=(230, 230, 230), outline=colour, width=3)
            draw.text((x0 + 8, y0 + 6), f"{w['app']} - {w['title']}", fill=(0, 0, 0))
        draw.text((40, HEIGHT - 40), self.typed[-60:], fill=(255, 220, 0))
        cx, cy = self.cursor
        draw.rectangle((cx - 16, cy - 16, cx + 16, cy + 16), outline=(255, 0, 0), width=4)
        # an action counter bar: every recorded action visibly changes the screen
        draw.rectangle((0, HEIGHT - 12, min(WIDTH, 24 * len(self.actions)), HEIGHT), fill=(0, 200, 120))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()

    def _focus(self, app: str) -> None:
        if not any(w["app"].lower() == app.lower() for w in self.windows):
            raise ToolError(
                f"no running app named {app!r} (apps: {', '.join(w['app'] for w in self.windows)})"
            )
        for w in self.windows:
            w["focused"] = w["app"].lower() == app.lower()

    def _apply(self, op: str, rec: dict[str, Any]) -> None:
        if rec.get("x") is not None and rec.get("y") is not None:
            self.cursor = to_pixels(rec["x"], rec["y"], WIDTH, HEIGHT)
        if op == "type":
            self.typed += rec.get("text") or ""
        elif op == "focus_app":
            self._focus(rec.get("target") or "")
        elif op == "drag" and rec.get("to"):
            self.cursor = to_pixels(rec["to"][0], rec["to"][1], WIDTH, HEIGHT)

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
        rec = {
            "op": op,
            "target": target,
            "x": x,
            "y": y,
            "text": text,
            "key": key,
            "delivery_mode": delivery_mode,
            "to": to,
            "direction": direction,
            "amount": amount,
        }
        if op == "screenshot":
            return ActResult(True, self.screenshot(), None, f"{WIDTH}x{HEIGHT} fake screen")
        if op == "wait":
            await asyncio.sleep(min(float(amount or 0), 0.05))  # fake time
            return ActResult(True, None, None, "waited")
        if op == "list_windows":
            st = await self.state()
            return ActResult(True, None, None, f"{len(st.windows)} windows", {"state": asdict(st)})
        if op not in MUTATING:
            raise ToolError(f"unsupported op {op!r}")
        pre = self.screenshot()
        if op in self.fail_ops:
            self.actions.append({**rec, "ok": False})
            return ActResult(False, pre, Verify(False, 0.0), f"{op} did not land (scripted failure)")
        self._apply(op, rec)
        self.actions.append({**rec, "ok": True})
        post = self.screenshot()
        verdict = self.scripted_verify.pop(0) if self.scripted_verify else compare(pre, post)
        return ActResult(True, post, verdict, f"{op} done")
