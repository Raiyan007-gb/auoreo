"""Routing `exec_command` / `write_stdin` to an execution-environment backend (W2-A).

The environment of a call is the session override (`SessionState.environment`, set by the
`use_environment` tool to a name from `settings.environments`) or else the selected
`backends.environment` backend (`local` by default). Permission rules and floors have
already run in the executor before anything here; a backend never widens them.

Named environments get their own backend instance (Settings + the entry's overrides),
cached in an `EnvironmentPool` on `services.ext`. The pool remembers which backend owns
each running process, so `write_stdin` reaches it after a later environment switch, and
releases every backend's per-session resources when the session is archived or deleted.
Every exec on a remote backend is audited as `environment.exec{backend, remote, cmd_hash}`.

M7 (`settings.m7.ssh.hosts`): names resolve through `core.hosts.resolve_environment`
(`settings.environments`, then `hosts/<name>.yaml`), and an SSH backend is used only after the
per-host `ssh` gate (`tools/ssh_gate.py`).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from auoreo.backends.environment import ExecChunk, ExecRequest
from auoreo.core import hosts
from auoreo.tools import ssh_gate
from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.shell_sessions import SessionScoped, ShellError, attached, clip_tokens

KIND = "environment"


@dataclass
class ProcRecord:
    backend: Any
    environment: str
    granted: str
    """The `bash` pattern exec_command was allowed under (write_stdin rides on it)."""


def cmd_hash(cmd: str) -> str:
    return hashlib.sha256(cmd.encode()).hexdigest()[:16]


def settings_for(settings: Any, entry: Any) -> Any:
    """Settings with a named environment's overrides applied (validated; unknown keys refused)."""
    return apply_overrides(settings, dict(getattr(entry, "model_extra", None) or {}))


def apply_overrides(settings: Any, overrides: dict[str, Any]) -> Any:
    unknown = sorted(k for k in overrides if k not in type(settings).model_fields)
    if unknown:
        raise ValueError(f"unknown settings in environment override: {', '.join(unknown)}")
    if not overrides:
        return settings
    return type(settings).model_validate({**settings.model_dump(), **overrides})


class EnvironmentPool(SessionScoped):
    """Backend instances for named environments + which backend owns each process."""

    def __init__(self, services: Any) -> None:
        super().__init__(services)
        self.instances: dict[str, Any] = {}
        self.procs: dict[str, dict[str, ProcRecord]] = {}
        self._closing: list[asyncio.Future[Any]] = []  # M7: replaced host-file backends shutting down
        self.epoch: dict[str, int] = {}
        """M7: times `name` was dropped (edited or revoked); part of the `ssh` grant key, so a
        grant never outlives the host definition it was given for."""

    def instance(self, name: str) -> Any:
        svc = self.services
        live = hosts.hosts_enabled(svc.settings)
        if name in self.instances and not live:
            return self.instances[name]
        env = hosts.resolve_environment(svc, name)
        cached = self.instances.get(name)
        if cached is not None and env is not None and self._same(cached, env):
            return cached
        if cached is not None:  # M7: the host file changed or is gone: never reuse its socket
            self._drop(name)
        if env is None:
            raise ToolError(hosts.unknown_message(svc, name))
        try:
            bdef = svc.harness.select_backend(KIND, env.backend)
            if bdef is None:
                raise KeyError(f"no '{KIND}' backend '{env.backend}'")
            inst = bdef.load()(apply_overrides(svc.settings, env.overrides))
        except (KeyError, ValueError) as exc:
            raise ToolError(f"environment '{name}': {exc}") from exc
        ssh_gate.attach(inst, svc.settings, svc.audit.record, name)
        self.instances[name] = inst
        return inst

    @staticmethod
    def _same(inst: Any, env: hosts.ResolvedEnv) -> bool:
        want = env.overrides.get("environment_ssh")
        return env.origin != "host-file" or getattr(inst, "cfg", None) == want

    def _drop(self, name: str) -> None:
        old = self.instances.pop(name)
        self.epoch[name] = self.epoch.get(name, 0) + 1
        for procs in self.procs.values():
            for pid in [p for p, rec in procs.items() if rec.backend is old]:
                procs.pop(pid)
        self._closing = [t for t in self._closing if not t.done()]
        self._closing.append(asyncio.ensure_future(old.close()))

    def record(self, session_id: str, proc_id: str, rec: ProcRecord) -> None:
        self.procs.setdefault(session_id, {})[proc_id] = rec

    def forget(self, session_id: str, proc_id: str) -> None:
        self.procs.get(session_id, {}).pop(proc_id, None)

    def _all(self) -> list[Any]:
        cached = getattr(self.services, "_backends", {}) or {}
        out = list(self.instances.values())
        if (default := cached.get(KIND)) is not None and default not in out:
            out.append(default)
        return out

    async def close_session(self, session_id: str) -> None:
        self.procs.pop(session_id, None)
        for inst in self._all():
            with contextlib.suppress(Exception):  # closing a session must not fail on a backend
                await inst.close_session(session_id)

    async def reap_idle(self) -> None:
        return None

    async def close_all(self) -> None:
        self.procs.clear()
        for inst in list(self.instances.values()):
            with contextlib.suppress(Exception):
                await inst.close()
        self.instances.clear()


def pool_for(services: Any) -> EnvironmentPool:
    pool = attached(services, "environments", EnvironmentPool)
    pool.ensure_tasks()
    return pool


def _services(ctx: ToolContext) -> Any:
    svc = ctx.services.get("services")
    if svc is None:
        raise ToolError(f"no services attached; backend '{KIND}' unavailable")
    return svc


def _bind(backend: Any, svc: Any) -> Any:
    bind = getattr(backend, "bind", None)
    if bind is not None:
        bind(svc)
    return backend


def resolve(ctx: ToolContext) -> tuple[str, Any]:
    """`(label, backend)` for this call: the session override, else the selected backend."""
    svc = _services(ctx)
    name = ctx.state.environment
    if name:
        return name, _bind(pool_for(svc).instance(name), svc)
    backend = ctx.backend(KIND)
    ssh_gate.attach(backend, svc.settings, svc.audit.record, ssh_gate.DEFAULT_NAME)
    return str(getattr(backend, "name", KIND)), _bind(backend, svc)


async def gate(ctx: ToolContext, label: str, backend: Any, *, tool: str, why: str | None = None) -> None:
    """M7: the per-host `ssh` permission for an SSH backend (no-op otherwise or flag off)."""
    svc = _services(ctx)
    if not ssh_gate.is_ssh(backend) or not hosts.hosts_enabled(svc.settings):
        return
    pool = pool_for(svc)
    named = pool.instances.get(label) is backend
    epoch = pool.epoch.get(label, 0) if named else 0
    target = ssh_gate.target_of(label if named else ssh_gate.DEFAULT_NAME, backend, epoch=epoch)
    await ssh_gate.gate_ssh(ctx, target, tool=tool, justification=why)


async def _guard[T](label: str, aw: Any) -> T:
    """Map backend failures to model-visible errors."""
    try:
        return await aw  # type: ignore[no-any-return]
    except ToolError:
        raise
    except ShellError as exc:
        raise ToolError(str(exc)) from exc
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise ToolError(f"environment '{label}' failed: {type(exc).__name__}: {exc}"[:2000]) from exc


def _preflight(label: str, backend: Any, tool: str) -> None:
    pre = getattr(backend, "preflight", None)
    if pre is None:
        return
    try:
        pre(tool)
    except ShellError as exc:
        raise ToolError(str(exc)) from exc
    except (KeyError, LookupError) as exc:
        raise ToolError(f"environment '{label}': {exc}") from exc


async def _audit(ctx: ToolContext, label: str, backend: Any, text: str, *, stdin: bool) -> None:
    if not getattr(backend, "remote", False):
        return
    svc = _services(ctx)
    detail = {
        "backend": str(getattr(backend, "name", "?")),
        "environment": label,
        "remote": True,
        "cmd_hash": cmd_hash(text),
    }
    if stdin:
        detail["stdin"] = True
    await svc.audit.record(actor="tool", action="environment.exec", session_id=ctx.session_id, detail=detail)


async def _workdir(ctx: ToolContext, backend: Any, raw: str | None) -> str | None:
    """Host path for `local` (outside cwd needs the edit permission); a path inside the
    environment otherwise (relative = under its working directory)."""
    if not getattr(backend, "host_paths", False):
        return raw or None
    workdir = ctx.resolve_path(raw or ".")
    root = ctx.cwd.resolve()
    if workdir != root and root not in workdir.parents:  # outside cwd: the edit rules decide
        await ctx.authorize("exec_command", "edit", str(workdir), default="ask")
    if not workdir.is_dir():
        raise ToolError(f"workdir is not a directory: {workdir}")
    return str(workdir)


async def exec_command(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    tty = bool(args.get("tty", False))
    label, backend = resolve(ctx)
    _preflight(label, backend, "exec_command")
    host = bool(getattr(backend, "host_paths", False))
    if tty and host and os.name != "posix":
        raise ToolError("tty=true needs a POSIX host (pty); run without tty")
    max_tokens = int(args.get("max_output_tokens", 4000))
    req = ExecRequest(
        cmd=str(args["cmd"]),
        workdir=await _workdir(ctx, backend, args.get("workdir")),
        tty=tty,
        yield_time_ms=int(args.get("yield_time_ms", 10000)),
        max_output_bytes=max_tokens * 4,
        env={} if getattr(backend, "remote", False) else dict(ctx.env),  # never ship host env off-box
    )
    prepare = getattr(backend, "prepare_session", None)
    if prepare is not None:
        prepare(ctx.session_id, ctx.cwd.resolve())
    await gate(ctx, label, backend, tool="exec_command")
    await _audit(ctx, label, backend, req.cmd, stdin=False)
    chunk: ExecChunk = await _guard(label, backend.exec_command(req, session_id=ctx.session_id))
    if chunk.exit_code is None and chunk.process_id:
        pool_for(_services(ctx)).record(ctx.session_id, chunk.process_id, ProcRecord(backend, label, req.cmd))
    return render(chunk, max_tokens, label=label, backend=backend)


def lookup(ctx: ToolContext, proc_id: str) -> ProcRecord:
    """The process `proc_id` of this session (refusing first under an enforcing sandbox)."""
    pool = pool_for(_services(ctx))
    rec = pool.procs.get(ctx.session_id, {}).get(proc_id)
    if rec is None:
        label, backend = resolve(ctx)
        _preflight(label, backend, "write_stdin")
        known = ", ".join(sorted(pool.procs.get(ctx.session_id, {}))) or "none"
        raise ToolError(f"no running exec_command session '{proc_id}' (live: {known})")
    if (
        pool.instances.get(rec.environment) is rec.backend
        and pool.instance(rec.environment) is not rec.backend
    ):
        # M7: the host file changed or was revoked under the process (`instance` dropped it)
        raise ToolError(
            f"no running exec_command session '{proc_id}' (environment '{rec.environment}' changed)"
        )
    _preflight(rec.environment, rec.backend, "write_stdin")
    return rec


async def write_stdin(ctx: ToolContext, rec: ProcRecord, proc_id: str, args: dict[str, Any]) -> ToolResult:
    chars = str(args.get("chars", ""))
    await gate(ctx, rec.environment, rec.backend, tool="write_stdin")
    if chars:
        await _audit(ctx, rec.environment, rec.backend, chars, stdin=True)
    pool = pool_for(_services(ctx))
    try:
        chunk: ExecChunk = await _guard(
            rec.environment,
            rec.backend.write_stdin(proc_id, chars, yield_time_ms=int(args.get("yield_time_ms", 2000))),
        )
    except ToolError as exc:
        if "no running exec_command session" in str(exc):
            pool.forget(ctx.session_id, proc_id)
        raise
    if chunk.exit_code is not None:
        pool.forget(ctx.session_id, proc_id)
    return render(chunk, int(args.get("max_output_tokens", 4000)), label=rec.environment, backend=rec.backend)


def render(chunk: ExecChunk, max_tokens: int, *, label: str, backend: Any) -> ToolResult:
    """`output`, then `[exit code N]` or `[running] session_id=…`."""
    lines = []
    dropped = int(getattr(chunk, "dropped", 0) or 0)
    if dropped:
        lines.append(f"[… {dropped} bytes dropped from the 1 MB output buffer …]")
    if chunk.output:
        lines.append(clip_tokens(chunk.output, max_tokens).rstrip("\n"))
    meta: dict[str, Any] = {"session_id": chunk.process_id, "tty": bool(getattr(chunk, "tty", False))}
    if (pid := getattr(chunk, "pid", None)) is not None:
        meta["pid"] = pid
    if not getattr(backend, "host_paths", False):
        meta["environment"] = label
        meta["remote"] = bool(getattr(backend, "remote", False))
    if chunk.exit_code is not None:
        lines.append(f"[exit code {chunk.exit_code}]")
        meta["exit_code"] = chunk.exit_code
        return ToolResult("\n".join(lines), is_error=chunk.exit_code != 0, meta=meta)
    lines.append(f"[running] session_id={chunk.process_id} — poll or send input with write_stdin")
    meta["running"] = True
    return ToolResult("\n".join(lines), meta=meta)


async def describe(settings: Any, harness: Any, audit: Any = None) -> list[tuple[str, bool, str, str]]:
    """`(name, ok, detail, remedy)` per named environment, for `auoreo doctor`. `audit` (the
    audit log) attributes an SSH socket opened here to the operator (`net.ssh`)."""
    svc = SimpleNamespace(settings=settings, harness=harness)
    limit = asyncio.Semaphore(4 if hosts.hosts_enabled(settings) else 1)

    async def one(name: str) -> tuple[str, bool, str, str]:
        env = hosts.resolve_environment(svc, name)
        assert env is not None
        async with limit:
            try:
                bdef = harness.select_backend(KIND, env.backend)
                if bdef is None:
                    raise KeyError(f"no '{KIND}' backend '{env.backend}'")
                inst = bdef.load()(apply_overrides(settings, env.overrides))
                if audit is not None:
                    ssh_gate.attach(inst, settings, audit.record, name, actor="operator")
                status = await asyncio.wait_for(inst.check(), 10)
                where = "remote" if getattr(inst, "remote", False) else "local"
                detail = (
                    f"{env.backend} ({where}): {status.detail or ('ready' if status.ok else 'not ready')}"
                )
                with contextlib.suppress(Exception):
                    await inst.close()
                return name, bool(status.ok), detail, status.remedy
            except Exception as exc:
                fix = "fix settings.environments" if env.origin == "settings" else f"fix hosts/{name}.yaml"
                return name, False, f"{env.backend}: {exc}"[:300], fix

    out = list(await asyncio.gather(*(one(n) for n in hosts.environment_names(svc))))
    for name in hosts.shadowed_hosts(svc):
        note = f"host file `{name}` shadowed by settings.environments"
        out.append((f"{name} (host file)", False, note, "rename the host file or the settings entry"))
    return out
