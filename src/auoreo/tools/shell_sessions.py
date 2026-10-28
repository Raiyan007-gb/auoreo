"""Persistent shell processes for `exec_command` / `write_stdin` (spec L271, Codex shape).

One registry per `Services` (kept in `services.ext["shell_sessions"]`, so `Services.close`
kills everything). Processes are scoped to the AUOREO session that started them, capped
per session, killed as a process group when the session is archived/deleted (bus events),
after `IDLE_S` without use, or at interpreter exit. Output goes to a 1 MB ring buffer.

`SessionScoped` + `attached` are shared with the LSP manager (`lsp_client.py`).
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import os
import secrets
import shlex
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auoreo.tools.context import ToolContext, ToolError, ToolResult

RING_BYTES = 1 << 20
IDLE_S = 30 * 60.0
_LIVE_PGIDS: set[int] = set()
_FALLBACK: dict[str, Any] = {}


@atexit.register
def _kill_leftovers() -> None:  # pragma: no cover - interpreter shutdown
    for pgid in list(_LIVE_PGIDS):
        with contextlib.suppress(OSError):
            os.killpg(pgid, signal.SIGKILL)


def attached[T](services: Any, key: str, factory: Callable[[Any], T]) -> T:
    """The per-`Services` singleton `key` (in `services.ext`, closed with the server)."""
    store = getattr(services, "ext", None) if services is not None else None
    if not isinstance(store, dict):
        store = _FALLBACK
    obj = store.get(key)
    if obj is None:
        obj = store[key] = factory(services)
    return obj


class SessionScoped:
    """Base for per-session resources: a bus watch for `session.archived`/`session.deleted`
    and an idle reaper, both started lazily on first use."""

    reap_every_s = 60.0

    def __init__(self, services: Any) -> None:
        self.services = services
        self._tasks: list[asyncio.Task[None]] = []
        self._sub: Any = None

    def ensure_tasks(self) -> None:
        if self._tasks and not all(t.done() for t in self._tasks):
            return
        self._tasks = [asyncio.create_task(self._reaper(), name=f"{type(self).__name__}:reaper")]
        bus = getattr(self.services, "bus", None)
        if bus is not None:
            self._sub = bus.subscribe(None)
            self._tasks.append(asyncio.create_task(self._watch(bus), name=f"{type(self).__name__}:watch"))

    async def _reaper(self) -> None:
        while True:
            await asyncio.sleep(self.reap_every_s)
            with contextlib.suppress(Exception):  # the reaper must survive
                await self.reap_idle()

    async def _watch(self, bus: Any) -> None:
        while True:
            if self._sub is None or self._sub.overflowed:
                if self._sub is not None:
                    bus.unsubscribe(self._sub)
                self._sub = bus.subscribe(None)
            ev = await self._sub.queue.get()
            if ev.type == "session.archived":
                await self.close_session(ev.aggregate_id)
            elif ev.type == "session.deleted" and isinstance(ev.data.get("session_id"), str):
                await self.close_session(ev.data["session_id"])

    async def close_session(self, session_id: str) -> None:
        raise NotImplementedError

    async def reap_idle(self) -> None:
        raise NotImplementedError

    async def close_all(self) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._tasks = []
        bus = getattr(self.services, "bus", None)
        if bus is not None and self._sub is not None:
            bus.unsubscribe(self._sub)
        self._sub = None
        await self.close_all()


class ShellError(Exception):
    """Model-visible failure (the tool handler turns it into a `ToolError`)."""


@dataclass
class ShellProcess:
    id: str
    session_id: str
    cmd: str
    tty: bool
    pid: int
    granted: str
    """The permission pattern (`cmd`) the executor allowed; `write_stdin` relies on it."""
    proc: asyncio.subprocess.Process
    master_fd: int | None = None
    buf: bytearray = field(default_factory=bytearray)
    base: int = 0  # absolute offset of buf[0] (bytes dropped from the ring)
    consumed: int = 0  # absolute offset already returned to the model
    eof: bool = False
    last_used: float = field(default_factory=time.monotonic)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    reader: asyncio.Task[None] | None = None

    @property
    def exit_code(self) -> int | None:
        return self.proc.returncode

    @property
    def finished(self) -> bool:
        return self.eof and self.proc.returncode is not None

    def feed(self, data: bytes) -> None:
        self.buf += data
        over = len(self.buf) - RING_BYTES
        if over > 0:
            del self.buf[:over]
            self.base += over
        self.changed.set()

    def take(self) -> tuple[str, int]:
        """New output since the last call, plus bytes lost to the ring buffer."""
        start = max(self.consumed, self.base)
        dropped = start - self.consumed
        data = bytes(self.buf[start - self.base :])
        self.consumed = self.base + len(self.buf)
        return data.decode("utf-8", errors="replace"), dropped

    def kill(self) -> None:
        with contextlib.suppress(OSError):
            os.killpg(self.pid, signal.SIGKILL)
        self.release()

    def release(self) -> None:
        """Drop OS resources (PTY fd, reader) without signalling the process group."""
        _LIVE_PGIDS.discard(self.pid)
        if self.master_fd is not None:
            with contextlib.suppress(Exception):
                asyncio.get_running_loop().remove_reader(self.master_fd)
            with contextlib.suppress(OSError):
                os.close(self.master_fd)
            self.master_fd = None
        if self.reader is not None:
            self.reader.cancel()


async def _pipe_reader(p: ShellProcess) -> None:
    assert p.proc.stdout is not None
    try:
        while chunk := await p.proc.stdout.read(65536):
            p.feed(chunk)
    finally:
        p.eof = True
        p.changed.set()


def _open_pty(p: ShellProcess) -> None:
    fd = p.master_fd
    assert fd is not None
    loop = asyncio.get_running_loop()
    os.set_blocking(fd, False)

    def on_readable() -> None:
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            return
        except OSError:  # EIO: the slave side closed (child exited)
            data = b""
        if data:
            p.feed(data)
            return
        loop.remove_reader(fd)
        p.eof = True
        p.changed.set()

    loop.add_reader(fd, on_readable)


class ShellRegistry(SessionScoped):
    """Live `exec_command` processes, per AUOREO session."""

    def __init__(self, services: Any, max_processes: int = 8, idle_s: float = IDLE_S) -> None:
        super().__init__(services)
        self.max_processes = max_processes
        self.idle_s = idle_s
        self._procs: dict[str, dict[str, ShellProcess]] = {}

    def live(self, session_id: str) -> dict[str, ShellProcess]:
        return self._procs.get(session_id, {})

    def find(self, proc_id: str) -> ShellProcess | None:
        """The live process `proc_id` in any session (ids are unique per registry)."""
        for procs in self._procs.values():
            if proc_id in procs:
                return procs[proc_id]
        return None

    def get(self, session_id: str, proc_id: str) -> ShellProcess:
        p = self.live(session_id).get(proc_id)
        if p is None:
            known = ", ".join(sorted(self.live(session_id))) or "none"
            raise ShellError(f"no running exec_command session '{proc_id}' (live: {known})")
        return p

    async def start(
        self,
        *,
        session_id: str,
        cmd: str,
        cwd: Path,
        env: dict[str, str],
        tty: bool,
        granted: str,
        argv: list[str] | None = None,
    ) -> ShellProcess:
        """Spawn `argv` (default `bash -lc <cmd>`; callers pass the sandbox-wrapped argv)."""
        self.ensure_tasks()
        mine = self._procs.setdefault(session_id, {})
        for pid, old in list(mine.items()):  # drop fully reported exits
            if old.finished and old.consumed >= old.base + len(old.buf):
                mine.pop(pid)
        if len(mine) >= self.max_processes:
            raise ShellError(
                f"too many live processes ({len(mine)}/{self.max_processes}): "
                f"{', '.join(sorted(mine))}. Finish or interrupt one (write_stdin chars='\\u0003' "
                "in tty mode) before starting another"
            )
        from auoreo.backends.sandbox import host_argv

        argv = host_argv(argv or ["bash", "-lc", cmd])  # Windows: Git Bash, not the WSL stub
        master: int | None = None
        if tty:
            import pty

            master, slave = pty.openpty()
            env = {"TERM": "dumb", **env}
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv, cwd=str(cwd), env=env, stdin=slave, stdout=slave, stderr=slave,
                    start_new_session=True,
                )  # fmt: skip
            finally:
                os.close(slave)
        else:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=str(cwd), env=env, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )  # fmt: skip
        _LIVE_PGIDS.add(proc.pid)
        pid_s = secrets.token_hex(3)
        while self.find(pid_s) is not None:  # unique across sessions (environment backends)
            pid_s = secrets.token_hex(3)
        p = ShellProcess(pid_s, session_id, cmd, tty, proc.pid, granted, proc, master_fd=master)
        if master is not None:
            _open_pty(p)
        else:
            p.reader = asyncio.create_task(_pipe_reader(p), name=f"shell:{pid_s}")
        mine[pid_s] = p
        return p

    async def write(self, p: ShellProcess, chars: str) -> None:
        if not chars:
            return
        if p.finished or p.proc.returncode is not None:
            raise ShellError(f"process {p.id} has exited (exit code {p.exit_code}); stdin is closed")
        data = chars.encode()
        try:
            if p.master_fd is not None:
                os.write(p.master_fd, data)
            else:
                assert p.proc.stdin is not None
                p.proc.stdin.write(data)
                await p.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            raise ShellError(f"stdin of {p.id} is closed: {exc}") from exc

    async def collect(self, p: ShellProcess, yield_s: float) -> tuple[str, int]:
        """Wait until the process exits (and its output is drained) or `yield_s` passes."""
        p.last_used = time.monotonic()
        deadline = time.monotonic() + yield_s
        while not p.finished:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            if p.eof:  # output closed; wait for the exit status
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(p.proc.wait(), left)
                continue
            p.changed.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(p.changed.wait(), min(left, 0.25))
            if p.proc.returncode is not None and not p.eof:
                p.changed.clear()  # exited: drain while output still flows, then stop
                try:
                    await asyncio.wait_for(p.changed.wait(), 0.1)
                except TimeoutError:
                    if not p.eof:  # a background child still holds the output open
                        p.release()
                        p.eof = True
        text, dropped = p.take()
        if p.finished:
            self._forget(p)
        return text, dropped

    def _forget(self, p: ShellProcess) -> None:
        p.release()
        self.live(p.session_id).pop(p.id, None)

    async def kill(self, p: ShellProcess) -> None:
        p.kill()
        with contextlib.suppress(TimeoutError, ProcessLookupError):
            await asyncio.wait_for(p.proc.wait(), 2.0)
        self.live(p.session_id).pop(p.id, None)

    async def close_session(self, session_id: str) -> None:
        for p in list(self._procs.pop(session_id, {}).values()):
            await self.kill(p)

    async def reap_idle(self) -> None:
        now = time.monotonic()
        for procs in list(self._procs.values()):
            for p in list(procs.values()):
                if now - p.last_used > self.idle_s:
                    await self.kill(p)

    async def close_all(self) -> None:
        for sid in list(self._procs):
            await self.close_session(sid)


def registry_for(services: Any) -> ShellRegistry:
    settings = getattr(services, "settings", None)
    cap = int(getattr(settings, "shell_max_processes", 8) or 8)
    return attached(services, "shell_sessions", lambda svc: ShellRegistry(svc, max_processes=cap))


def clip_tokens(text: str, max_tokens: int) -> str:
    """Head + tail clip at ~4 chars per token."""
    cap = max(200, max_tokens * 4)
    if len(text) <= cap:
        return text
    head, tail = text[: cap * 2 // 3], text[-cap // 3 :]
    return f"{head}\n… [{len(text) - len(head) - len(tail)} chars omitted] …\n{tail}"


UNWRAPPABLE = (
    "{tool} refused: the sandbox backend '{name}' cannot confine a long-lived process, and a "
    "persistent shell never runs unconfined under an enforcing backend. Use the one-shot `bash` "
    "tool instead (it runs through the sandbox), or select backends.sandbox = bwrap|seatbelt|none."
)


def _probe_spec(cwd: Path, mode: str = "workspace-write", net: str = "none") -> Any:
    from auoreo.backends.sandbox import ExecSpec

    return ExecSpec(argv=["true"], cwd=cwd, mode=mode, net=net)  # type: ignore[arg-type]


def sandbox_refusal(sandbox: Any, tool: str) -> str | None:
    """Why a persistent shell must refuse under `sandbox`: only when the backend cannot wrap a
    long-lived process (H1b — bwrap/seatbelt wrap it, `none` passes it through)."""
    name = str(getattr(sandbox, "name", "?"))
    wrap = getattr(sandbox, "wrap_long_lived", None)
    if wrap is None:  # a backend predating the hook: only an honest no-op one is accepted
        caps = sandbox.capabilities() if hasattr(sandbox, "capabilities") else {}
        return None if name == "none" and not any(caps.values()) else UNWRAPPABLE.format(tool=tool, name=name)
    return None if wrap(_probe_spec(Path.cwd())) is not None else UNWRAPPABLE.format(tool=tool, name=name)


def sandboxed_argv(
    sandbox: Any, cmd: str, *, root: Path, workdir: Path | None, tty: bool, tool: str
) -> list[str]:
    """`bash -lc <cmd>` wrapped by the sandbox backend with the calling tool's profile
    (`ExecPolicy` mode + net; `root` is the writable working directory). Raises ShellError
    when the backend cannot wrap it."""
    from auoreo.backends.sandbox import ExecSpec
    from auoreo.tools.context import CURRENT_EXEC, ExecPolicy

    inner = cmd
    if workdir is not None and workdir.resolve() != root.resolve():
        inner = f"cd {shlex.quote(str(workdir))} && {cmd}"
    argv = ["bash", "-lc", inner]
    if sandbox is None:
        return argv
    wrap = getattr(sandbox, "wrap_long_lived", None)
    if wrap is None:
        if why := sandbox_refusal(sandbox, tool):
            raise ShellError(why)
        return argv
    policy = CURRENT_EXEC.get() or ExecPolicy()
    spec = ExecSpec(argv=argv, cwd=root, mode=policy.mode, net=policy.net)  # type: ignore[arg-type]
    out = wrap(spec, tty=tty)
    if out is None:
        raise ShellError(UNWRAPPABLE.format(tool=tool, name=getattr(sandbox, "name", "?")))
    return list(out[0])


def refuse_if_sandboxed(ctx: ToolContext, tool: str) -> None:
    """Refuse only when the selected backend cannot confine a long-lived process."""
    if why := sandbox_refusal(ctx.backend("sandbox"), tool):
        raise ToolError(why)


def ctx_argv(ctx: ToolContext, cmd: str, *, workdir: Path | None, tty: bool, tool: str) -> list[str]:
    """`sandboxed_argv` for a tool handler: the session cwd is the writable root (an approved
    workdir outside it becomes the root instead)."""
    root = ctx.cwd.resolve()
    if workdir is not None and workdir != root and root not in workdir.parents:
        root = workdir
    try:
        return sandboxed_argv(ctx.backend("sandbox"), cmd, root=root, workdir=workdir, tty=tty, tool=tool)
    except ShellError as exc:
        raise ToolError(str(exc)) from exc


def render(p: ShellProcess, text: str, dropped: int, max_tokens: int) -> ToolResult:
    """`output`, then `[exit code N]` or `[running] session_id=…`."""
    lines = []
    if dropped:
        lines.append(f"[… {dropped} bytes dropped from the 1 MB output buffer …]")
    if text:
        lines.append(clip_tokens(text, max_tokens).rstrip("\n"))
    meta: dict[str, Any] = {"session_id": p.id, "pid": p.pid, "tty": p.tty}
    if p.finished:
        code = p.exit_code if p.exit_code is not None else -1
        lines.append(f"[exit code {code}]")
        meta["exit_code"] = code
        return ToolResult("\n".join(lines), is_error=code != 0, meta=meta)
    lines.append(f"[running] session_id={p.id} — poll or send input with write_stdin")
    meta["running"] = True
    return ToolResult("\n".join(lines), meta=meta)
