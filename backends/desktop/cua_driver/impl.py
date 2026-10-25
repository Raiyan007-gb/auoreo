"""cua-driver desktop backend: `cua-driver mcp` over MCP stdio (MCP SDK v2 `Client`).

Contract-tested only (no display here). Tool names follow the cua-driver MCP reference
(`libs/cua-driver`): `get_desktop_state` (screenshot + elements), `click`, `double_click`,
`right_click`, `type_text`, `press_key`, `hotkey`, `scroll`, `drag`, `list_windows`,
`list_apps`, `launch_app`. Every action passes `delivery_mode` (background default) and a
desktop target `{kind: "desktop", display_id: "primary"}` (or an `element_token`). Relative
0–1000 coordinates are mapped to pixels from the pre-action screenshot; the post-action
screenshot feeds `verify`.

The child gets a scrubbed environment (no API keys) with
`CUA_DRIVER_RS_TELEMETRY_ENABLED=0` unless `settings.cua_telemetry`. Floor version 0.20.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import os
import re
import shutil
import sys
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.desktop import MUTATING, ActResult, DesktopBackend, DesktopState, compare, to_pixels
from auoreo.core.secrets import ENV_ALLOWLIST
from auoreo.tools.context import ToolError

MIN_VERSION = (0, 20)
INSTALL = "install cua-driver ≥ 0.20 (https://github.com/trycua/cua, libs/cua-driver) and grant permissions"
DESKTOP = {"kind": "desktop", "display_id": "primary"}
SESSION = "auoreo"
_TOOLS = {
    "click": "click",
    "double_click": "double_click",
    "right_click": "right_click",
    "type": "type_text",
    "scroll": "scroll",
    "drag": "drag",
    "focus_app": "launch_app",
}


def parse_version(text: str) -> tuple[int, int, int] | None:
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


def child_env(telemetry: bool) -> dict[str, str]:
    env = {
        k: os.environ[k]
        for k in (*ENV_ALLOWLIST, "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY")
        if k in os.environ
    }
    if not telemetry:
        env["CUA_DRIVER_RS_TELEMETRY_ENABLED"] = "0"
        env["CUA_TELEMETRY"] = "0"
    return env


def no_display() -> bool:
    return sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )


def image_of(result: Any) -> bytes | None:
    for block in getattr(result, "content", None) or []:
        data = getattr(block, "data", None)
        if data and str(getattr(block, "mime_type", "")).startswith("image/"):
            return base64.b64decode(data)
    return None


def text_of(result: Any) -> str:
    return "\n".join(t for b in getattr(result, "content", None) or [] if (t := getattr(b, "text", None)))


class Backend(DesktopBackend):
    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self.command = str(getattr(settings, "cua_driver_command", "cua-driver"))
        self._client: Any = None
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._error: BaseException | None = None

    # ---------------------------------------------------------------- check
    async def check(self) -> BackendStatus:
        exe = shutil.which(self.command)
        if exe is None:
            return BackendStatus(False, f"{self.command} is not installed", remedy=INSTALL)
        if no_display():
            return BackendStatus(
                False, "no display (DISPLAY/WAYLAND_DISPLAY unset)", remedy="run in a desktop session"
            )
        try:
            proc = await asyncio.create_subprocess_exec(
                exe,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=child_env(False),
            )
            out, _ = await asyncio.wait_for(proc.communicate(), 10)
        except Exception as exc:
            return BackendStatus(
                False, f"{self.command} --version failed: {type(exc).__name__}", remedy=INSTALL
            )
        version = parse_version(out.decode("utf-8", "replace"))
        if version is None or version[:2] < MIN_VERSION:
            got = ".".join(map(str, version)) if version else "unknown"
            return BackendStatus(False, f"cua-driver {got} is older than 0.20", remedy=INSTALL)
        return BackendStatus(True, f"cua-driver {'.'.join(map(str, version))}")

    # ----------------------------------------------------------- connection
    async def _run(self) -> None:
        from mcp import Client, StdioServerParameters

        params = StdioServerParameters(
            command=self.command,
            args=["mcp"],
            env=child_env(bool(getattr(self.settings, "cua_telemetry", False))),
        )
        try:
            async with Client(params) as client:
                self._client = client
                self._ready.set()
                await self._stop.wait()
        except Exception as exc:
            self._error = exc
        finally:
            self._client = None
            self._ready.set()

    async def _call(self, tool: str, args: dict[str, Any]) -> Any:
        if self._task is None:
            status = await self.check()
            if not status.ok:
                raise ToolError(f"desktop backend cua_driver not ready: {status.detail}; {status.remedy}")
            self._task = asyncio.create_task(self._run(), name="cua-driver")
            await asyncio.wait_for(self._ready.wait(), 20)
        if self._client is None:
            raise ToolError(f"cua-driver MCP connection failed: {self._error}")
        result = await self._client.call_tool(tool, {"session": SESSION, **args})
        if getattr(result, "is_error", False):
            raise ToolError(f"cua-driver {tool} failed: {text_of(result)[:300]}")
        return result

    async def close(self) -> None:
        self._stop.set()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(self._task, 5)
        self._task = None

    # -------------------------------------------------------------- actions
    async def _screenshot(self) -> bytes | None:
        return image_of(await self._call("get_desktop_state", {}))

    def _args(self, op: str, rec: dict[str, Any], size: tuple[int, int]) -> tuple[str, dict[str, Any]]:
        args: dict[str, Any] = {"delivery_mode": rec["delivery_mode"]}
        if op == "focus_app":
            return "launch_app", {"name": rec["target"]}
        if op == "key":
            keys = [k.strip() for k in (rec["key"] or "").split("+") if k.strip()]
            tool = "hotkey" if len(keys) > 1 else "press_key"
            return tool, {
                **args,
                "target": DESKTOP,
                **({"keys": keys} if len(keys) > 1 else {"key": keys[0]}),
            }
        if rec["target"]:
            args["element_token"] = rec["target"]
        else:
            args["target"] = DESKTOP
        if rec["x"] is not None and rec["y"] is not None:
            args["x"], args["y"] = to_pixels(rec["x"], rec["y"], *size)
        if op == "type":
            args["text"] = rec["text"] or ""
        if op == "scroll":
            args["direction"], args["amount"] = rec["direction"] or "down", rec["amount"] or 3
        if op == "drag" and rec["to"]:
            args["to_x"], args["to_y"] = to_pixels(rec["to"][0], rec["to"][1], *size)
        return _TOOLS[op], args

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
        if op == "wait":
            await asyncio.sleep(max(0.0, min(float(amount or 1), 10.0)))
            return ActResult(True, None, None, "waited")
        if op == "screenshot":
            return ActResult(True, await self._screenshot(), None, "desktop screenshot")
        if op == "list_windows":
            result = await self._call("list_windows", {})
            return ActResult(True, None, None, text_of(result)[:4000])
        if op not in MUTATING:
            raise ToolError(f"unsupported op {op!r}")
        pre = await self._screenshot()
        size = (1920, 1080)
        if pre:
            from PIL import Image

            size = Image.open(io.BytesIO(pre)).size
        rec = {"target": target, "x": x, "y": y, "text": text, "key": key}
        rec |= {"delivery_mode": delivery_mode, "to": to, "direction": direction, "amount": amount}
        tool, args = self._args(op, rec, size)
        result = await self._call(tool, args)
        post = await self._screenshot()
        return ActResult(True, post, compare(pre, post), text_of(result)[:1000] or f"{tool} done")

    async def state(self) -> DesktopState:
        result = await self._call("list_windows", {})
        data = getattr(result, "structured_content", None) or {}
        windows = (
            [w for w in data.get("windows", []) if isinstance(w, dict)] if isinstance(data, dict) else []
        )
        apps = sorted({str(w.get("app") or w.get("app_name") or "") for w in windows} - {""})
        return DesktopState(apps=apps, windows=windows, display_count=int(data.get("display_count", 1) or 1))
