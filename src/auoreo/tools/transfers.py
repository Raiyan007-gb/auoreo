"""`env_transfer`: one file between this machine and the session's SSH environment (M7
hosts-c, D7, `m7.ssh.transfer`).

No host argument: the target is the session's selected environment, so a transfer only
reaches a host that passed the per-host `ssh` gate, and SFTP runs as a channel on that
granted connection (no new socket, no new destination: the documented `net: none`
exception in `docs/environments.md`). Checks, in order:

1. the flag; the environment is an SSH host (others support no transfer);
2. the session's `ssh` grant (`ssh_gate.gate_ssh`; asks only through `use_environment`);
3. permission `sftp` on `<env>:<direction>:<remote_path>`, one-shot: only a rule or a human
   reply grants (never a mode, never `always`, never `approvals.unattended`, and a chat app
   only with `m7.ssh.chat_grants`); the request carries `environment`/`host` for policies;
4. the local path as the file tools treat it: the secret-path floor, `read` (upload source)
   or `edit` (download target) outside cwd, a sensitive target always asks, plan mode
   blocks a download, symlinks are refused;
5. limits: one regular file, `m7.ssh.transfer_max_bytes`, an existing target is refused
   unless `overwrite`; a download lands in a temporary name (mode 0644) and is renamed.

Audit: `environment.transfer` before and `environment.transfer_done` after (bytes, sha256
or the error class name); never file content.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from auoreo.core import floors
from auoreo.core.permissions import is_sensitive_write
from auoreo.tools import environments, ssh_gate
from auoreo.tools.context import ToolContext, ToolError, ToolResult
from auoreo.tools.env_policy import fields_for

TOOL = "env_transfer"
PERMISSION = "sftp"
DIRECTIONS = ("upload", "download")


def _settings(ctx: ToolContext) -> Any:
    svc = ctx.services.get("services")
    if svc is None:
        raise ToolError("no services attached; env_transfer unavailable")
    ssh = svc.settings.m7.ssh
    if not (ssh.hosts and ssh.transfer):
        raise ToolError("env_transfer is off (settings.m7.ssh.transfer)")
    return svc


async def _authorize(ctx: ToolContext, svc: Any, label: str, pattern: str, detail: str) -> None:
    engine = ctx.services.get("engine")
    if engine is None:  # fail closed: a transfer always needs the decision
        raise ToolError(f"{PERMISSION} `{pattern}` needs a permission engine")
    context = {"tool": TOOL, "session_id": ctx.session_id, "cwd": str(ctx.cwd), **fields_for(svc, label)}
    decision = engine.evaluate(PERMISSION, pattern, "ask", interactive=ctx.interactive, context=context)
    by_rule = decision.action == "allow" and decision.reason != "mode"
    chat = ssh_gate.chat_grants(svc.settings)
    if not by_rule and decision.action != "deny" and ctx.interactive and not chat:
        await ssh_gate.refuse_platform(ctx, svc, f"{PERMISSION} `{pattern}`", f"{PERMISSION} {pattern}")
    await ctx.authorize(
        TOOL, PERMISSION, pattern, "ask", sticky=False, context=context, detail=detail, local_only=not chat
    )


def _facts(cwd: Path, raw: str, path: Path) -> dict[str, bool]:
    """Filesystem facts about the local side (sync: run off the event loop)."""
    return {
        "symlink": (cwd / raw).is_symlink() or Path(raw).expanduser().is_symlink(),
        "file": path.is_file(),
        "dir_ok": path.parent.is_dir(),
        "exists": path.exists() or path.is_symlink(),
    }


async def _local(ctx: ToolContext, direction: str, raw: str) -> tuple[Path, dict[str, bool]]:
    """The checked local path (resolved). Raises `ToolError` when the file rules refuse."""
    path = ctx.resolve_path(raw)
    if floors.is_secret_path(str(path)) or floors.is_secret_path(raw):
        raise ToolError("refused: secret files are never readable or writable by tools")
    facts = await asyncio.to_thread(_facts, ctx.cwd, raw, path)
    if facts["symlink"]:
        raise ToolError(f"refused: {raw} is a symlink (not followed)")
    root = ctx.cwd.resolve()
    outside = path != root and root not in path.parents
    if direction == "upload":
        if outside:
            await ctx.authorize(TOOL, "read", str(path), "ask")
        if not facts["file"]:
            raise ToolError(f"not a regular file: {path}")
        return path, facts
    if ctx.state.plan_mode:
        raise ToolError("refused: plan mode blocks writing files")
    if outside or is_sensitive_write(str(path)):  # a sensitive target always asks (floor)
        await ctx.authorize(TOOL, "edit", str(path), "ask")
    if not facts["dir_ok"]:
        raise ToolError(f"no such directory: {path.parent}")
    return path, facts


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _tmp_for(target: Path) -> Path:
    fd, name = tempfile.mkstemp(prefix=".env_transfer-", dir=target.parent)
    os.close(fd)
    return Path(name)


def _finish(tmp: Path, target: Path, cap: int, overwrite: bool) -> str:
    """Check the size, set 0644, hash and move into place (sync: run off the event loop)."""
    if tmp.stat().st_size > cap:
        raise ToolError(f"download over the {cap}-byte limit; aborted")
    tmp.chmod(0o644)
    digest = _sha256(tmp)
    if not overwrite and (target.exists() or target.is_symlink()):  # raced in meanwhile
        raise ToolError(f"local path exists: {target}")
    os.replace(tmp, target)
    return digest


def _drop(tmp: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        tmp.unlink()


async def _download(backend: Any, remote: str, target: Path, cap: int, overwrite: bool) -> tuple[int, str]:
    tmp = await asyncio.to_thread(_tmp_for, target)
    try:
        size = await backend.download(remote, str(tmp), max_bytes=cap)
        return int(size), await asyncio.to_thread(_finish, tmp, target, cap, overwrite)
    finally:
        await asyncio.to_thread(_drop, tmp)


async def transfer(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    svc = _settings(ctx)
    direction = str(args.get("direction", ""))
    if direction not in DIRECTIONS:
        raise ToolError("direction must be 'upload' or 'download'")
    remote, raw = str(args["remote_path"]), str(args["local_path"])
    overwrite = bool(args.get("overwrite", False))
    if not remote or "\n" in remote or "\x00" in remote:
        raise ToolError("remote_path must be one path")
    label, backend = environments.resolve(ctx)
    if not ctx.state.environment or not ssh_gate.is_ssh(backend):
        raise ToolError(
            "env_transfer works on an SSH environment only: select one with use_environment first"
        )
    grant = await environments.gate(ctx, label, backend, tool=TOOL)
    pattern = f"{label}:{direction}:{remote}"
    await _authorize(ctx, svc, label, pattern, f"{direction} {raw} {'->' if direction == 'upload' else '<-'} "
                     f"{label}:{remote}")  # fmt: skip
    local, facts = await _local(ctx, direction, raw)
    cap = int(svc.settings.m7.ssh.transfer_max_bytes)
    if direction == "download" and facts["exists"] and not overwrite:
        raise ToolError(f"local path exists: {local} (pass overwrite=true to replace it)")
    if direction == "upload" and (local_size := (await asyncio.to_thread(local.stat)).st_size) > cap:
        raise ToolError(f"{local} is {local_size} bytes, over the {cap}-byte limit")
    link = environments.linkage(ctx, svc, label, backend, TOOL)
    detail = {
        "environment": label, **link, "grant_id": grant, "direction": direction,
        "remote_path": remote[:500], "local_path": str(local)[:500], "overwrite": overwrite,
    }  # fmt: skip
    await svc.audit.record(
        actor="tool", action="environment.transfer", session_id=ctx.session_id, detail=detail
    )
    done: dict[str, Any] = {"call_id": link["call_id"], "ok": False, "bytes": 0, "sha256": None}
    size: int = 0
    try:
        if direction == "upload":
            digest = await asyncio.to_thread(_sha256, local)
            size = await environments._guard(
                label, backend.upload(str(local), remote, max_bytes=cap, overwrite=overwrite)
            )
        else:
            size, digest = await environments._guard(label, _download(backend, remote, local, cap, overwrite))
        done.update(ok=True, bytes=size, sha256=digest)
    except BaseException as exc:
        done["error"] = type(exc).__name__
        raise
    finally:
        await svc.audit.record(
            actor="tool", action="environment.transfer_done", session_id=ctx.session_id, detail=done
        )
    arrow = f"{local} -> {label}:{remote}" if direction == "upload" else f"{label}:{remote} -> {local}"
    return ToolResult(f"{direction}ed {size} bytes: {arrow} (sha256 {digest[:16]}…)", meta=done)
