"""Engine seam for the `terminal_pty*` and `jobs_*` tool folders (G4b, spec §9.3 L449, L982).

`terminal_pty` sessions are local pseudo-terminals kept in their own `PtyRegistry` (a
`ShellRegistry` attached as `services.ext["pty_sessions"]`, so archive/delete/idle/shutdown
reaping is shared with `exec_command`). Every PTY is scoped to the AUOREO session that
started it, and each op on one PTY holds that PTY's `asyncio.Lock` (Semaphore(1) per PTY).

Background jobs are the session's still-running processes: `exec_command` processes (in any
execution environment; the `EnvironmentPool` remembers which backend owns each) plus its
`terminal_pty` sessions. `jobs_*` list, read and kill them.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
import struct
import time
from dataclasses import dataclass
from typing import Any

from auoreo.core.permissions import PermissionEngine
from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.shell_sessions import (
    IDLE_S,
    ShellError,
    ShellProcess,
    ShellRegistry,
    attached,
    clip_tokens,
    registry_for,
)

_SIG_NAMES = ("INT", "TERM", "KILL", "HUP", "QUIT", "TSTP", "CONT", "USR1", "USR2", "WINCH")
SIGNALS: dict[str, int] = {
    n: int(getattr(signal, f"SIG{n}")) for n in _SIG_NAMES if hasattr(signal, f"SIG{n}")
}
KEYS: dict[str, str] = {
    "enter": "\r",
    "tab": "\t",
    "esc": "\x1b",
    "backspace": "\x7f",
    "ctrl-c": "\x03",
    "ctrl-d": "\x04",
    "ctrl-z": "\x1a",
    "ctrl-l": "\x0c",
    "ctrl-u": "\x15",
    "up": "\x1b[A",
    "down": "\x1b[B",
    "right": "\x1b[C",
    "left": "\x1b[D",
    "home": "\x1b[H",
    "end": "\x1b[F",
    "pageup": "\x1b[5~",
    "pagedown": "\x1b[6~",
}
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]|\r(?=\n)")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


class PtyRegistry(ShellRegistry):
    """Live `terminal_pty` sessions, per AUOREO session (one lock per PTY)."""

    def __init__(self, services: Any, max_processes: int = 8, idle_s: float = IDLE_S) -> None:
        super().__init__(services, max_processes=max_processes, idle_s=idle_s)
        self.locks: dict[str, asyncio.Lock] = {}
        self.started: dict[str, float] = {}

    def get(self, session_id: str, proc_id: str) -> ShellProcess:
        p = self.live(session_id).get(proc_id)
        if p is None:
            known = ", ".join(sorted(self.live(session_id))) or "none"
            raise ShellError(f"no terminal_pty session '{proc_id}' in this session (live: {known})")
        return p

    def lock(self, proc_id: str) -> asyncio.Lock:
        return self.locks.setdefault(proc_id, asyncio.Lock())

    def _forget(self, p: ShellProcess) -> None:
        super()._forget(p)
        self.locks.pop(p.id, None)
        self.started.pop(p.id, None)

    async def kill(self, p: ShellProcess) -> None:
        await super().kill(p)
        self.locks.pop(p.id, None)
        self.started.pop(p.id, None)


def pty_registry(services: Any) -> PtyRegistry:
    cfg = getattr(getattr(services, "settings", None), "tools", None)
    cap = int(getattr(cfg, "pty_max_sessions", 8) or 8)
    idle = float(getattr(cfg, "pty_idle_s", IDLE_S) or IDLE_S)
    reg = attached(services, "pty_sessions", lambda svc: PtyRegistry(svc, max_processes=cap, idle_s=idle))
    reg.max_processes, reg.idle_s = cap, idle  # settings may change at runtime
    return reg


def services_of(ctx: ToolContext) -> Any:
    svc = ctx.services.get("services")
    if svc is None:
        raise ToolError("no services attached; terminal sessions are unavailable")
    return svc


def set_winsize(p: ShellProcess, rows: int, cols: int) -> None:
    if p.master_fd is None:
        return
    import fcntl  # POSIX only, like the PTY itself
    import termios

    with contextlib.suppress(OSError):
        fcntl.ioctl(p.master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        os.killpg(p.pid, signal.SIGWINCH)


def authorize_stdin(ctx: ToolContext, sid: str, chars: str) -> None:
    """Same contract as `write_stdin`: `bash stdin:<sid>` may be denied by a rule or floor
    (an `ask` is covered by the grant the process was started under), and every written line
    passes the bash hardline floors."""
    engine = ctx.services.get("engine")
    if engine is not None:
        decision = engine.evaluate("bash", f"stdin:{sid}", "allow", interactive=ctx.interactive)
        if decision.action == "deny":
            raise ToolError(f"permission denied for bash `stdin:{sid}` ({decision.detail})")
    floors = engine or PermissionEngine([])
    for line in chars.replace("\r", "\n").splitlines():
        if line.strip() and (why := floors.floor("bash", line.strip(), interactive=ctx.interactive)):
            raise ToolError(f"permission denied (floor: {why}). Do not retry unchanged.")


def keys_to_chars(keys: list[str]) -> str:
    unknown = [k for k in keys if k.lower() not in KEYS]
    if unknown:
        raise ToolError(f"unknown key(s) {', '.join(unknown)} (known: {', '.join(KEYS)})")
    return "".join(KEYS[k.lower()] for k in keys)


def peek(p: ShellProcess, max_chars: int) -> str:
    """The last `max_chars` of the buffer (already-read output included), not consumed."""
    return bytes(p.buf[-max_chars * 4 :]).decode("utf-8", errors="replace")[-max_chars:]


def render_pty(p: ShellProcess, text: str, dropped: int, max_tokens: int, *, raw: bool = False) -> ToolResult:
    """Output (ANSI stripped unless `raw`), then `[exit code N]` or `[running] session_id=…`."""
    lines = []
    if dropped:
        lines.append(f"[… {dropped} bytes dropped from the 1 MB output buffer …]")
    if text:
        lines.append(clip_tokens(text if raw else strip_ansi(text), max_tokens).rstrip("\n"))
    meta: dict[str, Any] = {"session_id": p.id, "pid": p.pid}
    if p.finished:
        code = p.exit_code if p.exit_code is not None else -1
        lines.append(f"[exit code {code}]")
        meta["exit_code"] = code
        return ToolResult("\n".join(lines), is_error=False, meta=meta)
    lines.append(f"[running] session_id={p.id} — terminal_pty_send / terminal_pty_read")
    meta["running"] = True
    return ToolResult("\n".join(lines), meta=meta)


async def guarded[T](aw: Any) -> T:
    try:
        return await aw  # type: ignore[no-any-return]
    except ShellError as exc:
        raise ToolError(str(exc)) from exc


# ------------------------------------------------------------------ jobs
@dataclass
class Job:
    id: str
    kind: str  # "exec" | "pty"
    environment: str
    cmd: str
    pid: int | None
    running: bool
    age_s: float | None
    idle_s: float | None


def jobs_of(ctx: ToolContext) -> list[Job]:
    """Every live process this AUOREO session started (exec_command + terminal_pty)."""
    from auoreo.tools.environments import pool_for

    svc = services_of(ctx)
    now = time.monotonic()
    out: list[Job] = []
    local, pool = registry_for(svc), pool_for(svc)
    for pid, rec in sorted(pool.procs.get(ctx.session_id, {}).items()):
        p = local.find(pid) if getattr(rec.backend, "host_paths", False) else None
        if p is None and getattr(rec.backend, "host_paths", False):
            pool.forget(ctx.session_id, pid)  # exited and fully reported
            continue
        out.append(
            Job(
                pid,
                "exec",
                rec.environment,
                rec.granted,
                p.pid if p else None,
                not (p and p.finished),
                None,
                (now - p.last_used) if p else None,
            )
        )
    reg = pty_registry(svc)
    for pid, p in sorted(reg.live(ctx.session_id).items()):
        started = reg.started.get(pid)
        age = (now - started) if started else None
        out.append(Job(pid, "pty", "local", p.cmd, p.pid, not p.finished, age, now - p.last_used))
    return out


def find_job(ctx: ToolContext, job_id: str) -> Job:
    for job in jobs_of(ctx):
        if job.id == job_id:
            return job
    known = ", ".join(j.id for j in jobs_of(ctx)) or "none"
    raise ToolError(f"no background job '{job_id}' in this session (live: {known})")


def _secs(v: float | None) -> str:
    return "-" if v is None else f"{v:.0f}s"


def jobs_table(jobs: list[Job]) -> str:
    lines = ["| id | kind | env | pid | command | age | idle | state |", "|" + " --- |" * 8]
    for j in jobs:
        cmd = j.cmd.replace("|", "\\|").replace("\n", " ")[:80]
        state = "running" if j.running else "exited"
        lines.append(
            f"| {j.id} | {j.kind} | {j.environment} | {j.pid or '-'} | `{cmd}` | {_secs(j.age_s)} "
            f"| {_secs(j.idle_s)} | {state} |"
        )
    return "\n".join(lines)


async def kill_job(ctx: ToolContext, job: Job) -> None:
    """Kill one job: a PTY or local process group directly; a process in another
    environment through its backend's process table (`ProcTable`) when it has one."""
    from auoreo.tools.environments import pool_for

    svc = services_of(ctx)
    if job.kind == "pty":
        reg = pty_registry(svc)
        await reg.kill(reg.get(ctx.session_id, job.id))
        return
    pool = pool_for(svc)
    rec = pool.procs.get(ctx.session_id, {}).get(job.id)
    if rec is None:
        raise ToolError(f"no background job '{job.id}'")
    if getattr(rec.backend, "host_paths", False):
        local = registry_for(svc)
        if (p := local.find(job.id)) is not None:
            await local.kill(p)
    elif (table := getattr(rec.backend, "table", None)) is not None and job.id in table.procs:
        await table.procs.pop(job.id).kill()
    else:
        raise ToolError(
            f"environment '{rec.environment}' cannot kill processes directly; "
            f"send Ctrl-C with write_stdin(session_id='{job.id}', chars='\\u0003') (tty mode)"
        )
    pool.forget(ctx.session_id, job.id)
