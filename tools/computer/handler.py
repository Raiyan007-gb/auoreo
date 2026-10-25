"""The unified desktop act tool (M4d) over `ctx.backend("desktop")`.

Floors (unbypassable, before any backend call): key combos that lock the screen, log out,
shut down or kill the session, and `type` of text that pipes a download into a shell or wipes
the filesystem. `delivery_mode=foreground` needs its own approval (`computer_foreground`,
pattern = op), separate from the tool's `computer` permission. Every action is audited.
"""

import re

from auoreo.backends.desktop import MUTATING, DesktopBackend
from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.images import to_part

_ALIASES = {
    "command": "cmd",
    "meta": "cmd",
    "win": "super",
    "windows": "super",
    "option": "alt",
    "opt": "alt",
    "control": "ctrl",
    "delete": "del",
    "escape": "esc",
}
BLOCKED_COMBOS = [
    frozenset(c.split("+"))
    for c in (
        "cmd+ctrl+q",  # macOS lock screen
        "cmd+shift+q",  # macOS log out
        "cmd+alt+shift+q",  # macOS log out immediately
        "cmd+alt+esc",  # force quit dialog
        "ctrl+alt+del",  # Windows secure attention / Linux reboot
        "ctrl+alt+backspace",  # kill X server
        "ctrl+shift+esc",  # Windows task manager
        "super+l",  # Windows/GNOME lock
        "ctrl+alt+l",  # Linux lock
        "ctrl+cmd+power",  # macOS restart
        "cmd+alt+power",  # macOS sleep
        "alt+sysrq",  # magic SysRq
    )
]
BLOCKED_KEYS = frozenset({"power", "sleep", "poweroff", "shutdown", "logout", "lock"})
DANGEROUS_TEXT = [
    re.compile(r"\b(curl|wget|fetch|iwr|invoke-webrequest)\b[^\n|]*\|\s*(sudo\s+)?(ba|z|da|k|fi)?sh\b", re.I),
    re.compile(r"\brm\s+(-[a-z]*[rf][a-z]*\s+)+(--no-preserve-root\s+)?(/|~|\$home|/\*)(\s|$)", re.I),
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),  # fork bomb
    re.compile(r"\bmkfs(\.\w+)?\s+/dev/", re.I),
    re.compile(r"\bdd\s+[^\n]*\bof=/dev/(sd|nvme|disk|hd)", re.I),
    re.compile(r"\b(shutdown|reboot|halt|poweroff)\b(\s+-[a-z]+|\s+now)", re.I),
    re.compile(r"format\s+[a-z]:", re.I),
]


def combo(key: str) -> frozenset[str]:
    parts = [p.strip().lower() for p in re.split(r"[+\-](?=.)", key) if p.strip()]
    return frozenset(_ALIASES.get(p, p) for p in parts)


def key_floor(key: str, target: str | None) -> str | None:
    keys = combo(key)
    if keys & BLOCKED_KEYS:
        return f"key `{key}` (power/lock/logout) is blocked"
    if any(blocked <= keys for blocked in BLOCKED_COMBOS):
        return f"key combo `{key}` locks, logs out or kills the session and is blocked"
    if keys == {"alt", "f4"} and not target:
        return "alt+f4 without a target window could close the desktop session; pass the window as `target`"
    return None


def text_floor(text: str) -> str | None:
    for rx in DANGEROUS_TEXT:
        if rx.search(text):
            return "typing this text is blocked (download-to-shell, filesystem wipe or shutdown command)"
    return None


def parse_xy(raw: str | None) -> tuple[int, int] | None:
    m = re.fullmatch(r"\s*(\d{1,4})\s*,\s*(\d{1,4})\s*", raw or "")
    if not m:
        return None
    x, y = int(m.group(1)), int(m.group(2))
    if x > 1000 or y > 1000:
        raise ToolError("coordinates are relative: x and y must be within 0-1000")
    return x, y


def floors(op: str, args: dict) -> None:
    if op == "key":
        if not args.get("key"):
            raise ToolError("op=key needs `key`")
        why = key_floor(args["key"], args.get("target"))
        if why:
            raise ToolError(f"refused: {why}")
    if op == "type":
        if args.get("text") is None:
            raise ToolError("op=type needs `text`")
        why = text_floor(args["text"])
        if why:
            raise ToolError(f"refused: {why}")
    if op == "drag" and parse_xy(args.get("to")) is None:
        raise ToolError('op=drag needs `to` as "x,y"')
    if op == "focus_app" and not args.get("target"):
        raise ToolError("op=focus_app needs the app name as `target`")


async def _audit(ctx: ToolContext, args: dict, outcome: str, detail: dict) -> None:
    svc = ctx.services.get("services")
    if svc is None:
        return
    record = {k: v for k, v in args.items() if k != "text"}
    if "text" in args:
        record["text_chars"] = len(args["text"] or "")
    await svc.audit.record(
        actor="agent",
        action="computer.act",
        session_id=ctx.session_id or None,
        decision=outcome,
        detail={**record, **detail},
    )


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    op = args["op"]
    mode = args.get("delivery_mode", "background")
    try:
        floors(op, args)
        if mode == "foreground" and op in MUTATING:
            await ctx.authorize("computer", "computer_foreground", op, "ask")
    except ToolError as exc:
        await _audit(ctx, args, "refused", {"reason": str(exc)[:300]})
        raise
    backend = ctx.backend("desktop")
    if not isinstance(backend, DesktopBackend):
        raise ToolError(f"desktop backend {type(backend).__name__} does not implement DesktopBackend")
    target = args.get("target")
    xy = parse_xy(target) if op != "focus_app" else None
    result = await backend.act(
        op,
        target=None if xy else target,
        x=xy[0] if xy else None,
        y=xy[1] if xy else None,
        text=args.get("text"),
        key=args.get("key"),
        delivery_mode=mode,
        to=parse_xy(args.get("to")),
        direction=args.get("direction"),
        amount=args.get("amount"),
    )
    verify = {"matched": result.verify.matched, "score": result.verify.score} if result.verify else None
    await _audit(
        ctx, args, "ok" if result.ok else "failed", {"verify": verify, "detail": result.detail[:300]}
    )
    where = f" at {target}" if target else ""
    lines = [f"{op}{where} [{mode}]: {'ok' if result.ok else 'FAILED'} - {result.detail}"]
    if verify is not None:
        verdict = "screen changed" if verify["matched"] else "no visible change (suspected no-op)"
        lines.append(f"verify: matched={verify['matched']} score={verify['score']} ({verdict})")
    state = result.data.get("state")
    if state:
        lines += [f"displays: {state['display_count']}; apps: {', '.join(state['apps'])}"]
        lines += [
            f"- [{w.get('id')}] {w.get('app')}: {w.get('title')}{' (focused)' if w.get('focused') else ''}"
            for w in state["windows"]
        ]
    images = []
    meta: dict = {"op": op, "ok": result.ok, "verify": verify}
    if result.screenshot_png:
        image, info = await to_part(result.screenshot_png, f"desktop after {op}")
        images.append(image)
        meta["images"] = 1
        lines.append(f"screenshot: {info['width']}x{info['height']} {image.media_type}")
    return ToolResult("\n".join(lines), is_error=not result.ok, meta=meta, images=images)
