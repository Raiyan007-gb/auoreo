"""G5a: `auoreo exec`, `auoreo apply`, `auoreo review` (spec §18).

- `exec -- <cmd>` runs through the selected **sandbox** backend (`--via environment` uses the
  selected environment backend instead). The command was typed by a human, so there is no
  ask, but the hardline/configured floors still refuse destructive commands, the child gets
  a scrubbed environment, and every run is audited.
- `apply <patch>` applies a V4A patch with the `apply_patch` tool's engine (same parser,
  write guards and secret-file floor; `--dry-run` only validates).
- `review [path] [--diff]` runs the `/review` command through the agent loop.
"""

from __future__ import annotations

import asyncio
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from auoreo.cli.common import err_console, settings
from auoreo.core.services import Services

VIA = ("sandbox", "environment")
MODES = ("read-only", "workspace-write", "danger-full-access")
NETS = ("none", "proxy", "direct")


async def _floor(svc: Services, command: str) -> str | None:
    from auoreo.core.permissions import PermissionEngine
    from auoreo.harness.orchestrator import floors

    return PermissionEngine([], floors(svc.harness)).floor("bash", command, interactive=True)


async def _audit(svc: Services, command: str, via: str, code: int | None) -> None:
    await svc.audit.record(
        actor="cli",
        action="exec",
        decision="allow",
        reason=f"auoreo exec via {via}",
        detail={"command": command[:500], "exit_code": code},
    )


async def _via_sandbox(svc: Services, argv: list[str], mode: str, net: str, timeout: float) -> int:
    from auoreo.backends.sandbox import ExecSpec, SandboxError
    from auoreo.core.env_custom import shell_env
    from auoreo.core.secrets import child_env

    backend = svc.backend("sandbox")
    spec = ExecSpec(
        argv=argv,
        cwd=svc.cwd,
        env=child_env([], svc.secrets) | shell_env(svc, "cli:exec"),
        timeout_s=timeout,
        mode=mode,  # type: ignore[arg-type]
        net=net,  # type: ignore[arg-type]
        merge_stderr=True,
    )
    try:
        result = await backend.run(spec)
    except SandboxError as exc:
        err_console.print(f"[red]sandbox refused: {exc}[/red]")
        return 2
    sys.stdout.write(result.stdout)
    sys.stdout.flush()
    note = f"{result.backend}{' (enforced)' if result.enforced else ' (not enforced)'}"
    if result.timed_out:
        err_console.print(f"[red]timed out after {timeout:g}s[/red]")
    err_console.print(f"[dim]exit {result.exit_code} · sandbox {note}[/dim]")
    return int(result.exit_code)


async def _via_environment(svc: Services, command: str, timeout: float) -> int:
    from auoreo.backends.environment import ExecRequest
    from auoreo.core.env_custom import shell_env
    from auoreo.core.secrets import child_env

    backend = svc.backend("environment")
    if (bind := getattr(backend, "bind", None)) is not None:
        bind(svc)
    if (preflight := getattr(backend, "preflight", None)) is not None:
        try:
            preflight("exec_command")
        except Exception as exc:
            err_console.print(f"[red]{getattr(backend, 'name', 'environment')} refused: {exc}[/red]")
            return 2
    env = {} if getattr(backend, "remote", False) else child_env([], svc.secrets) | shell_env(svc, "cli:exec")
    yield_ms = int(min(timeout, 30) * 1000)
    workdir = str(svc.cwd) if getattr(backend, "host_paths", False) else None
    req = ExecRequest(cmd=command, workdir=workdir, yield_time_ms=yield_ms, env=env)
    chunk = await backend.exec_command(req, session_id="cli-exec")
    waited = yield_ms / 1000
    while True:
        sys.stdout.write(chunk.output)
        sys.stdout.flush()
        if chunk.exit_code is not None or not chunk.process_id or waited >= timeout:
            break
        chunk = await backend.write_stdin(chunk.process_id, "", yield_time_ms=yield_ms)
        waited += yield_ms / 1000
    await backend.close_session("cli-exec")
    if chunk.exit_code is None:
        err_console.print(f"[red]still running after {timeout:g}s; stopped waiting[/red]")
        return 124
    err_console.print(f"[dim]exit {chunk.exit_code} · environment {getattr(backend, 'name', '?')}[/dim]")
    return int(chunk.exit_code)


def exec_(
    cmd: Annotated[list[str], typer.Argument(help="Command (one string runs under `bash -c`).")],
    via: Annotated[str, typer.Option(help="sandbox (default) | environment")] = "sandbox",
    mode: Annotated[str, typer.Option(help="Sandbox mode: read-only|workspace-write|danger-full-access")] = (
        "workspace-write"
    ),
    net: Annotated[str, typer.Option(help="Sandbox network: none|proxy|direct")] = "none",
    timeout: Annotated[float, typer.Option(help="Seconds before the command is killed.")] = 120.0,
) -> None:
    """Run a command through the sandbox / environment backend (floors apply, audited)."""
    bad = {"--via": (via, VIA), "--mode": (mode, MODES), "--net": (net, NETS)}
    for flag, (value, choices) in bad.items():
        if value not in choices:
            err_console.print(f"[red]{flag} must be one of {', '.join(choices)}[/red]")
            raise typer.Exit(2)
    command = cmd[0] if len(cmd) == 1 else shlex.join(cmd)
    argv = ["bash", "-c", cmd[0]] if len(cmd) == 1 else list(cmd)

    async def go() -> int:
        svc = await Services.create(settings(), cwd=Path.cwd(), with_memory=False, with_mcp=False)
        try:
            if why := await _floor(svc, command):
                err_console.print(f"[red]refused: {why}[/red]")
                await svc.audit.record(actor="cli", action="exec", decision="deny", reason=why)
                return 2
            code = (
                await _via_sandbox(svc, argv, mode, net, timeout)
                if via == "sandbox"
                else await _via_environment(svc, command, timeout)
            )
            await _audit(svc, command, via, code)
            return code
        finally:
            await svc.close()

    raise typer.Exit(asyncio.run(go()))


def apply(
    patch_file: Annotated[Path, typer.Argument(help="V4A patch file ('-' = stdin).")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Validate and show the plan only.")] = False,
) -> None:
    """Apply a `*** Begin Patch` (V4A) patch with the apply_patch engine (transactional)."""
    from auoreo.tools.context import ToolContext, ToolError

    text = sys.stdin.read() if str(patch_file) == "-" else _read(patch_file)

    async def go() -> tuple[bool, str]:
        st = settings()
        svc = await Services.create(st, cwd=Path.cwd(), with_memory=False, with_mcp=False)
        try:
            tool = svc.harness.tools.get("apply_patch")
            if tool is None:
                return False, "the apply_patch tool is not available in this harness"
            ctx = ToolContext("cli-apply", svc.cwd, st.home, harness=svc.harness, services={"services": svc})
            try:
                result = await tool.handler()({"patch": text, "dry_run": dry_run}, ctx)
            except ToolError as exc:
                return False, str(exc)
            await svc.audit.record(
                actor="cli",
                action="apply_patch",
                decision="allow",
                reason="auoreo apply",
                detail={"dry_run": dry_run},
            )
            if isinstance(result, str):
                return True, result
            return not result.is_error, result.content
        finally:
            await svc.close()

    ok, out = asyncio.run(go())
    (print if ok else lambda s: err_console.print(f"[red]{s}[/red]"))(out)
    if not ok:
        raise typer.Exit(1)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        err_console.print(f"[red]cannot read {path}: {exc}[/red]")
        raise typer.Exit(2) from exc


def review(
    target: Annotated[str | None, typer.Argument(help="Path, commit, branch or description.")] = None,
    diff: Annotated[
        bool, typer.Option("--diff", help="Review the working-tree diff (git diff HEAD).")
    ] = False,
    staged: Annotated[bool, typer.Option("--staged", help="With --diff: only staged changes.")] = False,
    print_only: Annotated[bool, typer.Option("--print", help="Print only the final review.")] = False,
    agent: Annotated[str | None, typer.Option(help="Agent to run the review with.")] = None,
    model: Annotated[str | None, typer.Option("--model", "-m")] = None,
    gateway: Annotated[str | None, typer.Option(help="litellm | fake")] = None,
) -> None:
    """Run the `/review` command (code-review skill) on a path, ref or the current diff."""
    from auoreo.cli.commands.run import _pretty
    from auoreo.cli.run_support import RunPlan, run_in_process

    if not target and not diff:
        err_console.print("[red]give a path/ref to review or --diff[/red]")
        raise typer.Exit(2)
    subject = target or ""
    if diff:
        args = ["git", "diff", "--cached" if staged else "HEAD", "--", *([target] if target else [])]
        proc = subprocess.run(args, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            err_console.print(f"[red]{' '.join(args)} failed: {proc.stderr.strip()}[/red]")
            raise typer.Exit(2)
        if not proc.stdout.strip():
            err_console.print("nothing to review: the diff is empty")
            return
        subject = f"this diff{f' of {target}' if target else ''}:\n\n```diff\n{proc.stdout}\n```"
    plan = RunPlan(
        prompt=f"/review {subject}", agent=agent, model=model, fmt="print" if print_only else "text"
    )
    raise typer.Exit(asyncio.run(run_in_process(settings(gateway=gateway), plan, _pretty)))
