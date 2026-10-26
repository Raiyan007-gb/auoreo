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

I8: `middle_click` (`click` with `button: middle`), `set_value` (element token + value),
`list_apps`; `opts.mode` (som|vision|ax) is passed to `get_desktop_state`; a sticky
`opts.window {pid, window_id}` becomes a window target; foreground pointer ops move first and
wait `opts.settle_ms` (moveAndSettle 50 ms); `--no-overlay` per `i8.computer_use.no_overlay`
(per-OS default); `health_report()` runs `cua-driver health_report --json` (exit 0/1/2,
`display_count == 0` guard, macOS accessibility + screen-recording readiness); `unhide()` asks the
driver to restore hidden apps at turn end (best effort).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import os
import re
import shutil
import sys
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.desktop import (
    MUTATING,
    OBSERVE,
    ActResult,
    DesktopBackend,
    DesktopState,
    HealthReport,
    compare,
    no_overlay_default,
    to_pixels,
)
from auoreo.core.secrets import ENV_ALLOWLIST
from auoreo.core.settings_i8 import i8_of
from auoreo.tools.context import ToolError

MIN_VERSION = (0, 20)
INSTALL = "install cua-driver ≥ 0.20 (https://github.com/trycua/cua, libs/cua-driver) and grant permissions"
DESKTOP = {"kind": "desktop", "display_id": "primary"}
SESSION = "auoreo"
_TOOLS = {
    "click": "click",
    "double_click": "double_click",
    "right_click": "right_click",
    "middle_click": "click",
    "set_value": "set_value",
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


def elements_of(result: Any) -> list[dict[str, Any]]:
    data = getattr(result, "structured_content", None) or {}
    items = data.get("elements", []) if isinstance(data, dict) else []
    out = []
    for e in items:
        if isinstance(e, dict) and (tok := e.get("element_token") or e.get("token")):
            out.append({"token": str(tok), "role": e.get("role"), "label": e.get("label") or e.get("name")})
    return out


def parse_health(code: int, out: str) -> HealthReport:
    """`cua-driver health_report --json` → HealthReport (exit code wins; display_count=0 → 2;
    macOS needs accessibility AND screen recording, else 1)."""
    try:
        data = json.loads(out) if out.strip() else {}
    except ValueError:
        data = {}
    data = data if isinstance(data, dict) else {}
    count = int(data.get("display_count", 0) or 0)
    ax, rec = data.get("accessibility"), data.get("screen_recording")
    detail = str(data.get("detail") or data.get("status") or f"exit {code}")
    if count == 0:
        return HealthReport(2, "display_count=0 (no display attached)", 0, ax, rec)
    level = min(2, max(0, code))
    if sys.platform == "darwin" and not (ax and rec):
        level = max(level, 1)
        detail = "grant Accessibility and Screen Recording to com.trycua.driver"
    return HealthReport(level, detail, count, ax, rec)


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

    @property
    def no_overlay(self) -> bool:
        configured = i8_of(self.settings).computer_use.no_overlay
        return no_overlay_default() if configured is None else bool(configured)

    async def health_report(self) -> HealthReport:
        exe = shutil.which(self.command)
        if exe is None:
            return HealthReport(2, f"{self.command} is not installed")
        try:
            proc = await asyncio.create_subprocess_exec(
                exe,
                "health_report",
                "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=child_env(False),
            )
            out, _ = await asyncio.wait_for(proc.communicate(), 15)
        except Exception as exc:
            return HealthReport(2, f"health_report failed: {type(exc).__name__}")
        return parse_health(proc.returncode or 0, out.decode("utf-8", "replace"))

    async def unhide(self) -> None:
        if self._client is None:
            return
        with contextlib.suppress(Exception):
            await self._client.call_tool("unhide_apps", {"session": SESSION})

    # ----------------------------------------------------------- connection
    async def _run(self) -> None:
        from mcp import Client, StdioServerParameters

        params = StdioServerParameters(
            command=self.command,
            args=["mcp", *(["--no-overlay"] if self.no_overlay else [])],
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
    async def _screenshot(self, mode: str = "vision") -> bytes | None:
        return image_of(await self._call("get_desktop_state", {"mode": mode}))

    def _args(self, op: str, rec: dict[str, Any], size: tuple[int, int]) -> tuple[str, dict[str, Any]]:
        args: dict[str, Any] = {"delivery_mode": rec["delivery_mode"]}
        window = (rec.get("opts") or {}).get("window")
        desktop = (
            {"kind": "window", "pid": window.get("pid"), "window_id": window.get("window_id")}
            if isinstance(window, dict) and window.get("window_id") is not None
            else DESKTOP
        )
        if op == "set_value":
            return "set_value", {
                **args,
                "element_token": rec["target"],
                "value": (rec.get("opts") or {}).get("value", ""),
            }
        if op == "focus_app":
            return "launch_app", {"name": rec["target"]}
        if op == "key":
            keys = [k.strip() for k in (rec["key"] or "").split("+") if k.strip()]
            tool = "hotkey" if len(keys) > 1 else "press_key"
            return tool, {
                **args,
                "target": desktop,
                **({"keys": keys} if len(keys) > 1 else {"key": keys[0]}),
            }
        if rec["target"]:
            args["element_token"] = rec["target"]
        else:
            args["target"] = desktop
        if op == "middle_click":
            args["button"] = "middle"
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
        opts: dict[str, Any] | None = None,
    ) -> ActResult:
        opts = dict(opts or {})
        mode = str(opts.get("mode") or "som")
        if op == "wait":
            await asyncio.sleep(max(0.0, min(float(amount or 1), 10.0)))
            return ActResult(True, None, None, "waited")
        if op == "screenshot":
            result = await self._call("get_desktop_state", {"mode": mode})
            data = {"mode": mode, "elements": elements_of(result)}
            return ActResult(
                True, image_of(result), None, text_of(result)[:4000] or "desktop screenshot", data
            )
        if op in OBSERVE:
            result = await self._call(op, {})
            return ActResult(True, None, None, text_of(result)[:4000])
        if op not in MUTATING:
            raise ToolError(f"unsupported op {op!r}")
        pre = await self._screenshot()
        size = (1920, 1080)
        if pre:
            from PIL import Image

            size = Image.open(io.BytesIO(pre)).size
        rec = {"target": target, "x": x, "y": y, "text": text, "key": key, "opts": opts}
        rec |= {"delivery_mode": delivery_mode, "to": to, "direction": direction, "amount": amount}
        tool, args = self._args(op, rec, size)
        settle = int(opts.get("settle_ms") or 0)
        if settle and delivery_mode == "foreground" and "x" in args:
            await self._call(
                "move_cursor", {"x": args["x"], "y": args["y"], "target": args.get("target", DESKTOP)}
            )
            await asyncio.sleep(settle / 1000)
        result = await self._call(tool, args)
        if opts.get("capture_after"):
            state = await self._call("get_desktop_state", {"mode": mode})
            post, data = image_of(state), {"elements": elements_of(state)}
        else:
            post, data = await self._screenshot(), {}
        return ActResult(True, post, compare(pre, post), text_of(result)[:1000] or f"{tool} done", data)

    async def state(self) -> DesktopState:
        result = await self._call("list_windows", {})
        data = getattr(result, "structured_content", None) or {}
        windows = (
            [w for w in data.get("windows", []) if isinstance(w, dict)] if isinstance(data, dict) else []
        )
        apps = sorted({str(w.get("app") or w.get("app_name") or "") for w in windows} - {""})
        return DesktopState(apps=apps, windows=windows, display_count=int(data.get("display_count", 1) or 1))
