"""In-memory fake desktop: a window list, Pillow-drawn screenshots, recorded actions.

Each screenshot draws the windows (focused one highlighted), a cursor at the last pointer
position and the last typed text, so a mutating op changes the picture and the generic
pre/post `compare` verifies it. Tests may script outcomes: `backend.scripted_verify` (a
list of `Verify`, consumed in order) overrides the computed verdict; `backend.fail_ops`
makes those ops report `ok=False`.

I8: every state read bumps `gen` and returns element tokens `e<gen>-<window>` (window) and
`e<gen>-<window>-field` (its text field, target of `set_value`); modes `som` (overlay boxes +
elements), `vision` (pixels only), `ax` (tree text, no image); a clipboard (`clipboard`);
`focus_external(app)` simulates the user switching windows behind the agent's back;
`unhidden` counts turn-end `unhide()` calls; `settles` records moveAndSettle pauses.
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
            {"id": 1, "pid": 101, "app": "Finder", "title": "Home", "focused": True, "value": ""},
            {"id": 2, "pid": 202, "app": "TextEdit", "title": "Untitled", "focused": False, "value": ""},
        ]
        self.actions: list[dict[str, Any]] = []
        self.cursor: tuple[int, int] = (WIDTH // 2, HEIGHT // 2)
        self.typed = ""
        self.scripted_verify: list[Verify] = []
        self.fail_ops: set[str] = set()
        self.gen = 0
        self.clipboard: str | None = "previous clipboard"
        self.clipboard_supported = True
        self.unhidden = 0
        self.settles: list[int] = []
        self.display_count = 1

    async def check(self) -> BackendStatus:
        return BackendStatus(True, "fake desktop (test backend; in-memory windows)")

    async def state(self) -> DesktopState:
        apps = sorted({w["app"] for w in self.windows})
        wins = [{k: v for k, v in w.items() if k != "value"} for w in self.windows]
        return DesktopState(apps=apps, windows=wins, display_count=self.display_count)

    async def focused(self) -> dict[str, Any] | None:
        w = next((w for w in self.windows if w["focused"]), None)
        return {"pid": w["pid"], "window_id": w["id"], "app": w["app"], "title": w["title"]} if w else None

    def focus_external(self, app: str) -> None:
        for w in self.windows:
            w["focused"] = w["app"] == app

    async def get_clipboard(self) -> str | None:
        return self.clipboard if self.clipboard_supported else None

    async def set_clipboard(self, text: str) -> bool:
        if not self.clipboard_supported:
            return False
        self.clipboard = text
        return True

    async def unhide(self) -> None:
        self.unhidden += 1

    def elements(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i, w in enumerate(self.windows):
            x, y = 30 + i * 60 + 180, 30 + i * 50 + 110
            rel = (round(x * 1000 / WIDTH), round(y * 1000 / HEIGHT))
            out.append({"token": f"e{self.gen}-{w['id']}", "role": "window", "label": w["title"], "xy": rel})
            out.append(
                {"token": f"e{self.gen}-{w['id']}-field", "role": "textfield", "label": "text", "xy": rel}
            )
        return out

    def screenshot(self, overlay: bool = False) -> bytes:
        img = Image.new("RGB", (WIDTH, HEIGHT), (40, 44, 52))
        draw = ImageDraw.Draw(img)
        for i, w in enumerate(self.windows):
            x0, y0 = 30 + i * 60, 30 + i * 50
            colour = (90, 140, 230) if w["focused"] else (120, 120, 120)
            draw.rectangle((x0, y0, x0 + 360, y0 + 220), fill=(230, 230, 230), outline=colour, width=3)
            draw.text((x0 + 8, y0 + 6), f"{w['app']} - {w['title']} {w['value'][-20:]}", fill=(0, 0, 0))
            if overlay:
                draw.rectangle((x0 + 2, y0 + 2, x0 + 30, y0 + 20), fill=(255, 0, 255))
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

    def _window_of(self, token: str | None) -> dict[str, Any] | None:
        if not token or not token.startswith("e"):
            return None
        try:
            wid = int(token.split("-")[1])
        except (IndexError, ValueError):
            return None
        return next((w for w in self.windows if w["id"] == wid), None)

    def _apply(self, op: str, rec: dict[str, Any]) -> None:
        if rec.get("x") is not None and rec.get("y") is not None:
            self.cursor = to_pixels(rec["x"], rec["y"], WIDTH, HEIGHT)
        if op == "type":
            self.typed += rec.get("text") or ""
        elif op == "focus_app":
            self._focus(rec.get("target") or "")
        elif op == "drag" and rec.get("to"):
            self.cursor = to_pixels(rec["to"][0], rec["to"][1], WIDTH, HEIGHT)
        elif op == "set_value":
            w = self._window_of(rec.get("target"))
            if w is None:
                raise ToolError("set_value needs an element token of a text field")
            w["value"] = str((rec.get("opts") or {}).get("value", ""))
        elif op == "key" and (rec.get("key") or "").lower() in {"cmd+v", "ctrl+v"}:
            self.typed += self.clipboard or ""

    def _observe(self, op: str, mode: str, opts: dict[str, Any]) -> ActResult:
        self.gen += 1
        if op == "list_apps":
            apps = sorted({w["app"] for w in self.windows})
            return ActResult(True, None, None, f"{len(apps)} apps: {', '.join(apps)}", {"apps": apps})
        if op == "list_windows":
            st = asdict_state(self)
            return ActResult(True, None, None, f"{len(st['windows'])} windows", {"state": st})
        data: dict[str, Any] = {"mode": mode}
        if mode == "ax":
            data["elements"] = self.elements()
            tree = "\n".join(f"[{e['token']}] {e['role']} {e['label']!r}" for e in data["elements"])
            return ActResult(True, None, None, f"accessibility tree:\n{tree}", data)
        if mode == "som":
            data["elements"] = self.elements()
        overlay = mode == "som" and not opts.get("no_overlay")
        return ActResult(True, self.screenshot(overlay), None, f"{WIDTH}x{HEIGHT} fake screen", data)

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
        opts = dict(opts or {})
        mode = str(opts.get("mode") or "som")
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
            "opts": opts,
        }
        if op in ("screenshot", "list_windows", "list_apps"):
            return self._observe(op, mode, opts)
        if op == "wait":
            await asyncio.sleep(min(float(amount or 0), 0.05))  # fake time
            return ActResult(True, None, None, "waited")
        if op not in MUTATING:
            raise ToolError(f"unsupported op {op!r}")
        pre = self.screenshot()
        if op in self.fail_ops:
            self.actions.append({**rec, "ok": False})
            return ActResult(False, pre, Verify(False, 0.0), f"{op} did not land (scripted failure)")
        if opts.get("settle_ms") and x is not None:
            self.settles.append(int(opts["settle_ms"]))
        self._apply(op, rec)
        self.gen += 1  # the UI changed: earlier element tokens are stale
        self.actions.append({**rec, "ok": True})
        post = self.screenshot()
        verdict = self.scripted_verify.pop(0) if self.scripted_verify else compare(pre, post)
        data: dict[str, Any] = {}
        if opts.get("capture_after"):
            data["elements"] = self.elements()
        return ActResult(True, post, verdict, f"{op} done", data)


def asdict_state(b: Backend) -> dict[str, Any]:
    apps = sorted({w["app"] for w in b.windows})
    wins = [{k: v for k, v in w.items() if k != "value"} for w in b.windows]
    return asdict(DesktopState(apps=apps, windows=wins, display_count=b.display_count))
