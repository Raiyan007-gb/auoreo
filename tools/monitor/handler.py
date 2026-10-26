"""monitor: watch a streamed command, a polled command or a growing file until a regex
matches, the stream ends, or the timeout passes (spec §2.3 monitor loop, §2.7 ops).

Permissions: the manifest permission is `monitor` (allow); the watched target is checked
again in the handler — `bash <cmd>` (default ask; floors apply) or `read <path>` (secret
files refused). Streaming runs the command unsandboxed (refused under an enforcing sandbox,
like exec_command); poll mode runs each run through the selected sandbox backend.
Progress is emitted as `tool.progress {tool: "monitor", ...}` at most once a second.
"""

import asyncio
import contextlib
import os
import re
import signal
import time
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path

from auoreo.backends.sandbox import ExecSpec, SandboxError
from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.shell_sessions import clip_tokens, ctx_argv

_SECRET = re.compile(r"(^|/)(\.env(\.[^/]*)?|secrets\.db)$")
KEEP_LINES = 200


class Watch:
    def __init__(self, ctx: ToolContext, pattern: re.Pattern[str] | None, deadline: float) -> None:
        self.ctx, self.pattern, self.deadline = ctx, pattern, deadline
        self.recent: deque[str] = deque(maxlen=KEEP_LINES)
        self.seen = 0
        self.match: str | None = None
        self._last_emit = 0.0
        self._pending: list[str] = []

    @property
    def left(self) -> float:
        return self.deadline - time.monotonic()

    async def feed(self, line: str, *, whole: str | None = None) -> bool:
        """Record a line (or a poll run's output); True once the condition matched."""
        self.seen += 1
        self.recent.append(line)
        self._pending.append(line)
        hay = whole if whole is not None else line
        if self.pattern is not None and (m := self.pattern.search(hay)):
            self.match = line if whole is None else _line_of(whole, m.start())
        await self.progress(force=self.match is not None)
        return self.match is not None

    async def progress(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not self._pending or (not force and now - self._last_emit < 1.0):
            return
        self._last_emit = now
        lines, self._pending = self._pending[-5:], []
        with contextlib.suppress(Exception):  # progress must never fail the watch
            await self.ctx.emit("tool.progress", {"tool": "monitor", "lines": lines, "seen": self.seen})


def _line_of(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start : end if end >= 0 else len(text)]


async def _stream(ctx: ToolContext, cmd: str, w: Watch) -> tuple[str, int | None]:
    argv = ctx_argv(ctx, cmd, workdir=None, tty=False, tool="monitor (streaming cmd)")
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(ctx.cwd), env=dict(ctx.env), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True,
        limit=1 << 20,
    )  # fmt: skip
    assert proc.stdout is not None
    try:
        while w.left > 0:
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), w.left)
            except TimeoutError:
                return "timeout", None
            if not raw:
                code = await proc.wait()
                return "exited", code
            if await w.feed(raw.decode("utf-8", errors="replace").rstrip("\r\n")):
                return "matched", None
        return "timeout", None
    finally:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), 2)


async def _poll(ctx: ToolContext, cmd: str, every: float, w: Watch) -> tuple[str, int | None]:
    sandbox = ctx.backend("sandbox")
    policy = ctx.exec_policy
    code: int | None = None
    while w.left > 0:
        started = time.monotonic()
        spec = ExecSpec(
            argv=["bash", "-c", cmd], cwd=ctx.cwd, env=dict(ctx.env), timeout_s=max(1.0, min(w.left, 120.0)),
            mode=policy.mode, net=policy.net, merge_stderr=True,  # type: ignore[arg-type]
        )  # fmt: skip
        try:
            res = await sandbox.run(spec)
        except SandboxError as exc:
            raise ToolError(f"[sandbox: {exc}]") from exc
        code = res.exit_code
        out = (res.stdout + res.stderr).rstrip("\n")
        summary = f"[run {w.seen + 1}, exit {code}{', timed out' if res.timed_out else ''}] {out[-300:]}"
        if await w.feed(summary, whole=out):
            return "matched", code
        await asyncio.sleep(max(0.0, min(every - (time.monotonic() - started), w.left)))
    return "timeout", code


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _read_from(path: Path, pos: int, size: int) -> bytes:
    with path.open("rb") as fh:
        fh.seek(pos)
        return fh.read(min(size - pos, 1 << 20))


async def _lines(path: Path, from_start: bool, w: Watch) -> AsyncIterator[str]:
    pos = 0 if from_start else await asyncio.to_thread(_size, path)
    partial = ""
    while w.left > 0:
        size = await asyncio.to_thread(_size, path)
        if size < pos:  # truncated / rotated
            pos, partial = 0, ""
        if size > pos:
            data = await asyncio.to_thread(_read_from, path, pos, size)
            pos += len(data)
            *done, partial = (partial + data.decode("utf-8", errors="replace")).split("\n")
            for line in done:
                yield line.rstrip("\r")
            continue
        await w.progress()
        await asyncio.sleep(min(0.25, max(w.left, 0)))


async def _follow(ctx: ToolContext, path: Path, from_start: bool, w: Watch) -> tuple[str, int | None]:
    async for line in _lines(path, from_start, w):
        if await w.feed(line):
            return "matched", None
    return "timeout", None


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    cmd, raw_path = args.get("cmd"), args.get("path")
    if bool(cmd) == bool(raw_path):
        raise ToolError("give exactly one of cmd or path")
    if args.get("every_s") and not cmd:
        raise ToolError("every_s only applies to cmd")
    try:
        flags = re.IGNORECASE if args.get("ignore_case") else 0
        pattern = re.compile(args["until"], flags) if args.get("until") else None
    except re.error as exc:
        raise ToolError(f"invalid until regex: {exc}") from exc
    svc = ctx.services.get("services")
    cap = int(getattr(getattr(getattr(svc, "settings", None), "tools", None), "monitor_max_s", 600))
    timeout = min(int(args.get("timeout_s", 60)), cap)
    w = Watch(ctx, pattern, time.monotonic() + timeout)
    if cmd:
        await ctx.authorize("monitor", "bash", str(cmd), default="ask")
        every = args.get("every_s")
        status, code = await (_poll(ctx, cmd, float(every), w) if every else _stream(ctx, cmd, w))
        target = f"`{cmd}`"
    else:
        path = ctx.resolve_path(str(raw_path))
        if _SECRET.search(str(path)) and not str(path).endswith(".env.example"):
            raise ToolError(f"refusing to read secret file {path}")
        await ctx.authorize("monitor", "read", str(path), default="allow")
        status, code = await _follow(ctx, path, bool(args.get("from_start")), w)
        target = str(path)
    await w.progress(force=True)
    head = {
        "matched": f"matched after {w.seen} line(s): {w.match}",
        "exited": f"command exited (code {code}) after {w.seen} line(s) without a match"
        if pattern
        else f"command exited (code {code}) after {w.seen} line(s)",
        "timeout": f"timed out after {timeout}s ({w.seen} line(s) seen)"
        + (" without a match" if pattern else ""),
    }[status]
    tail = clip_tokens("\n".join(w.recent), int(args.get("max_output_tokens", 2000)))
    text = f"monitor {target}: {head}" + (f"\n--- recent output ---\n{tail}" if tail else "")
    meta = {"status": status, "lines": w.seen, "match": w.match, "exit_code": code}
    return ToolResult(text, is_error=pattern is not None and status != "matched", meta=meta)
