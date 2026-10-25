"""The `auoreo-input` data channel: remote input ("teleop beyond view", F1).

Only when `Settings.teleop_input_enabled` is true and the offer asked for `input: true`
(the router answers 403 otherwise); a client-created `auoreo-input` channel on any other peer
is ignored. Inbound frames are JSON

    {"id": "e1", "op": "click"|"type"|"key"|"scroll"|"move", "x": 0-1000, "y": 0-1000,
     "text": "...", "key": "ctrl+s", "direction": "up"|"down"|"left"|"right", "amount": 1-50}

Each event becomes one `computer` tool call through the normal `ToolExecutor` path
(`PermissionEngine` floors → Starlark policies → rules, hooks, audit) in the peer's session
with `interactive=True`. An `ask` decision is an ordinary approval request on the WS/SSE
control plane (`permission.requested`); nothing about it travels on this channel (§19.12).

Server → client acks: `{"id", "ok", "detail"}` only (the first line of the tool result,
≤ 200 chars). Events run one at a time in arrival order. At most `RATE_LIMIT` events per
rolling second per peer; the rest are dropped silently and counted (`dropped`). Each
accepted event is audited as `teleop.input{rtc_id, op}` — typed text only as its length.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections import deque
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)

INPUT_LABEL = "auoreo-input"
RATE_LIMIT = 30
MAX_FRAME_BYTES = 16 * 1024
MAX_QUEUE = 64
DIRECTIONS = frozenset({"up", "down", "left", "right"})


class InputError(ValueError):
    """A malformed input event (acked with ok=false, never executed)."""


def _xy(ev: dict[str, Any], required: bool) -> str | None:
    x, y = ev.get("x"), ev.get("y")
    if x is None and y is None and not required:
        return None
    if not all(isinstance(v, int | float) and not isinstance(v, bool) and 0 <= v <= 1000 for v in (x, y)):
        raise InputError("x and y must be numbers within 0-1000")
    return f"{round(x)},{round(y)}"  # type: ignore[arg-type]


def _click(ev: dict[str, Any]) -> dict[str, Any]:
    return {"op": "click", "target": _xy(ev, True)}


def _type(ev: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(ev.get("text"), str) or not ev["text"]:
        raise InputError("op=type needs `text`")
    return {"op": "type", "text": ev["text"]}


def _key(ev: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(ev.get("key"), str) or not ev["key"]:
        raise InputError("op=key needs `key`")
    return {"op": "key", "key": ev["key"]}


def _scroll(ev: dict[str, Any]) -> dict[str, Any]:
    args: dict[str, Any] = {"op": "scroll", "direction": ev.get("direction") or "down"}
    if args["direction"] not in DIRECTIONS:
        raise InputError("direction must be up, down, left or right")
    if (target := _xy(ev, False)) is not None:
        args["target"] = target
    amount = ev.get("amount")
    if amount is not None:
        if not isinstance(amount, int) or isinstance(amount, bool) or not 1 <= amount <= 50:
            raise InputError("amount must be an integer within 1-50")
        args["amount"] = amount
    return args


def _move(ev: dict[str, Any]) -> dict[str, Any]:
    _xy(ev, True)
    raise InputError("move is not supported: the computer tool has no hover op")


OPS = {"click": _click, "type": _type, "key": _key, "scroll": _scroll, "move": _move}


def to_computer_args(ev: dict[str, Any]) -> dict[str, Any]:
    """Map one input event to `computer` tool arguments (raises `InputError`)."""
    fn = OPS.get(str(ev.get("op")))
    if fn is None:
        raise InputError(f"op must be one of {', '.join(OPS)}")
    return fn(ev)


class TeleopChannel:
    def __init__(self, svc: Services, rtc_id: str, session_id: str, channel: Any) -> None:
        self.svc = svc
        self.rtc_id = rtc_id
        self.session_id = session_id
        self.channel = channel
        self.dropped = 0
        self.accepted = 0
        self._recent: deque[float] = deque()
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(MAX_QUEUE)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._worker(), name=f"teleop:{self.rtc_id}")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    # ------------------------------------------------------------ inbound
    def feed(self, message: Any) -> None:
        """aiortc `message` callback: parse, rate-limit, queue (never blocks)."""
        now = time.monotonic()
        while self._recent and now - self._recent[0] >= 1.0:
            self._recent.popleft()
        if len(self._recent) >= RATE_LIMIT:
            self.dropped += 1
            return
        self._recent.append(now)
        if not isinstance(message, str) or len(message.encode()) > MAX_FRAME_BYTES:
            self._ack(None, False, "frames must be JSON text ≤ 16 KiB")
            return
        try:
            ev = json.loads(message)
        except json.JSONDecodeError:
            self._ack(None, False, "invalid JSON")
            return
        if not isinstance(ev, dict):
            self._ack(None, False, "frames must be JSON objects")
            return
        try:
            self._queue.put_nowait(ev)
        except asyncio.QueueFull:
            self.dropped += 1
        self.start()

    def _ack(self, ev_id: Any, ok: bool, detail: str) -> None:
        if getattr(self.channel, "readyState", "open") != "open":
            return
        ident = ev_id if isinstance(ev_id, str | int) and not isinstance(ev_id, bool) else None
        with contextlib.suppress(Exception):
            self.channel.send(json.dumps({"id": ident, "ok": ok, "detail": detail[:200]}))

    # ------------------------------------------------------------- worker
    async def _worker(self) -> None:
        while True:
            ev = await self._queue.get()
            try:
                ok, detail = await self.handle(ev)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # one bad event must not stop the channel
                log.exception("teleop %s: input event failed", self.rtc_id)
                ok, detail = False, f"internal error ({type(exc).__name__})"
            self._ack(ev.get("id"), ok, detail)

    async def handle(self, ev: dict[str, Any]) -> tuple[bool, str]:
        op = str(ev.get("op"))
        try:
            args = to_computer_args(ev)
        except InputError as exc:
            return False, str(exc)
        self.accepted += 1
        detail: dict[str, Any] = {"rtc_id": self.rtc_id, "op": op}
        if "text" in args:
            detail["text_chars"] = len(args["text"])
        await self.svc.audit.record(
            actor="client", action="teleop.input", session_id=self.session_id, detail=detail
        )
        return await self._run(args)

    async def _run(self, args: dict[str, Any]) -> tuple[bool, str]:
        from auoreo.gateway.types import ToolCall
        from auoreo.tools.context import ToolContext

        svc = self.svc
        tool = svc.harness.tools.get("computer")
        if tool is None:
            return False, "the computer tool is not installed"
        coord = svc.coordinator
        rt = await coord.runtime(self.session_id)
        spec = coord.runner._spec(rt, rt.agent, None)
        engine = coord.runner._engine(rt, spec)
        sid = self.session_id

        async def emit(event_type: str, data: dict[str, Any]) -> None:
            await svc.emit(sid, event_type, {"source": "teleop", **data})

        ctx = ToolContext(
            session_id=sid,
            cwd=svc.cwd,
            home=svc.settings.home,
            harness=svc.harness,
            state=rt.state,
            interactive=True,
            emit=emit,
            services={"memory": svc.memory, "services": svc, "engine": engine, "spec": spec},
        )
        call = ToolCall(id=f"teleop_{uuid.uuid4().hex[:12]}", name="computer", arguments=json.dumps(args))
        outcome = await coord.runner.executor.run(call, tools={"computer": tool}, ctx=ctx, engine=engine)
        res = outcome.result
        first = (res.content or "").strip().splitlines()
        return not res.is_error, first[0] if first else ("failed" if res.is_error else "ok")
