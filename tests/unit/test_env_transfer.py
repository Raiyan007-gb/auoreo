"""M7 hosts-c D7: `env_transfer` rides the session's ssh grant and needs its own one-shot
`sftp` decision; the local side follows the file rules. Adversarial cases first."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from auoreo.core.permissions import PermissionEngine, Rule
from auoreo.core.services import Services
from auoreo.tools.environments import pool_for
from tests.unit import test_env_ssh
from tests.unit.test_env_ssh import BASE, KEY_NAME, WHY, SshServer, audit_rows, host_file, session, tool

ssh_server = test_env_ssh.ssh_server
pytestmark = test_env_ssh.pytestmark
SSH = Rule(permission="ssh", pattern="lab", action="allow")


def on(services: Services, **kw: Any) -> None:
    services.settings.m7.ssh.hosts = True
    services.settings.m7.ssh.transfer = True
    for k, v in kw.items():
        setattr(services.settings.m7.ssh, k, v)


async def selected(services: Services, workdir: Path, srv: SshServer, rules: list[Rule], **kw: Any) -> Any:
    host_file(services, srv)
    engine = PermissionEngine([*BASE, SSH, *rules], **kw.pop("engine_kw", {}))
    ctx = session(services, workdir, engine, **kw)
    text, err, _ = await tool(services, ctx, engine, "use_environment", {"name": "lab", "justification": WHY})
    assert not err, text
    return engine, ctx


def leftovers(d: Path) -> list[str]:
    return [n for n in os.listdir(d) if n.startswith(".env_transfer-")]


def remote_file(tmp_path: Path, name: str = "app.log", data: bytes = b"line-1\nline-2\n") -> Path:
    d = tmp_path / "remote"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_bytes(data)
    return p


async def test_refused_when_off_or_on_a_local_environment(
    services: Services, ssh_server: SshServer, workdir: Path, tmp_path: Path
) -> None:
    services.settings.m7.ssh.hosts = True
    engine = PermissionEngine([*BASE, SSH, Rule(permission="sftp", pattern="*", action="allow")])
    ctx = session(services, workdir, engine)
    args = {"direction": "download", "local_path": "x", "remote_path": str(remote_file(tmp_path))}
    text, err, _ = await tool(services, ctx, engine, "env_transfer", args)
    assert err and "m7.ssh.transfer" in text
    on(services)
    text, err, _ = await tool(services, ctx, engine, "env_transfer", args)
    assert err and "SSH environment only" in text and not (workdir / "x").exists()


async def test_download_with_rules_lands_in_cwd_with_two_rows(
    services: Services, ssh_server: SshServer, workdir: Path, tmp_path: Path
) -> None:
    on(services)
    src = remote_file(tmp_path)
    sftp = Rule(permission="sftp", pattern="lab:download:*", action="allow")
    engine, ctx = await selected(services, workdir, ssh_server, [sftp])
    args = {"direction": "download", "local_path": "logs/app.log", "remote_path": str(src)}
    (workdir / "logs").mkdir()
    text, err, meta = await tool(services, ctx, engine, "env_transfer", args)
    assert not err, text
    got = workdir / "logs" / "app.log"
    assert got.read_bytes() == src.read_bytes() and (got.stat().st_mode & 0o777) == 0o644
    assert not leftovers(workdir / "logs")
    start = (await audit_rows(services, "environment.transfer"))[0].detail
    done = (await audit_rows(services, "environment.transfer_done"))[0].detail
    grant = (await audit_rows(services, "net.ssh_grant"))[0].detail["grant_id"]
    assert start["grant_id"] == grant and start["direction"] == "download" and start["host_id"]
    assert start["call_id"] == done["call_id"] == "c"
    assert done["ok"] and done["sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()
    assert "line-1" not in json.dumps([start, done])  # never content
    # the download rule does not cover an upload
    up = {"direction": "upload", "local_path": "logs/app.log", "remote_path": str(tmp_path / "remote" / "up")}
    text, err, _ = await tool(services, ctx, engine, "env_transfer", up)
    assert err and "non-interactive" in text and not (tmp_path / "remote" / "up").exists()
    await pool_for(services).close()


async def test_needs_the_ssh_grant_first(
    services: Services, ssh_server: SshServer, workdir: Path, tmp_path: Path
) -> None:
    on(services)
    host_file(services, ssh_server)
    engine = PermissionEngine([*BASE, Rule(permission="sftp", pattern="*", action="allow")])
    ctx = session(services, workdir, engine)
    ctx.state.environment = "lab"  # selected, but never granted (no ssh rule, no human)
    args = {"direction": "download", "local_path": "x", "remote_path": str(remote_file(tmp_path))}
    text, err, _ = await tool(services, ctx, engine, "env_transfer", args)
    assert err and "ssh" in text and not (workdir / "x").exists()
    assert not await audit_rows(services, "environment.transfer")
    await pool_for(services).close()


async def test_unattended_approve_and_modes_never_grant_sftp(
    services: Services, ssh_server: SshServer, workdir: Path, tmp_path: Path
) -> None:
    on(services)
    services.settings.approvals.unattended.cron = "approve"
    engine, ctx = await selected(services, workdir, ssh_server, [], engine_kw={"mode": "yolo"})
    ctx.services["unattended"] = "cron"
    args = {"direction": "download", "local_path": "x", "remote_path": str(remote_file(tmp_path))}
    text, err, _ = await tool(services, ctx, engine, "env_transfer", args)
    assert err and "sftp" in text and "non-interactive" in text and not (workdir / "x").exists()
    await pool_for(services).close()


async def test_interactive_card_is_one_shot_and_local_only(
    services: Services, ssh_server: SshServer, workdir: Path, tmp_path: Path
) -> None:
    on(services)
    engine, ctx = await selected(services, workdir, ssh_server, [], interactive=True)
    args = {"direction": "download", "local_path": "x", "remote_path": str(remote_file(tmp_path))}
    call = asyncio.create_task(tool(services, ctx, engine, "env_transfer", args))
    for _ in range(300):
        if services.approvals.pending():
            break
        await asyncio.sleep(0.01)
    card = services.approvals.pending()[0]
    assert card.permission == "sftp" and card.pattern == f"lab:download:{args['remote_path']}"
    assert card.one_shot and card.local_only
    await services.approvals.reply(card.request_id, "always")  # counts as once
    text, err, _ = await call
    assert not err, text
    assert engine.evaluate("sftp", card.pattern).action != "allow"  # nothing remembered
    await pool_for(services).close()


@pytest.mark.parametrize(
    ("direction", "local", "why"),
    [
        ("upload", ".env", "secret files"),
        ("download", ".env", "secret files"),
        ("download", "secrets.db", "secret files"),
        ("download", ".git/hooks/pre-commit", "sensitive"),
    ],
)
async def test_local_file_rules_apply(
    services: Services,
    ssh_server: SshServer,
    workdir: Path,
    tmp_path: Path,
    direction: str,
    local: str,
    why: str,
) -> None:
    on(services)
    (workdir / ".env").write_text("OPENROUTER_API_KEY=sk-or-secret\n")
    (workdir / ".git" / "hooks").mkdir(parents=True)
    rules = [
        Rule(permission="sftp", pattern="*", action="allow"),
        Rule(permission="edit", pattern="*", action="allow"),
    ]
    engine, ctx = await selected(services, workdir, ssh_server, rules)
    remote = str(remote_file(tmp_path) if direction == "download" else tmp_path / "remote-up")
    text, err, _ = await tool(
        services,
        ctx,
        engine,
        "env_transfer",
        {"direction": direction, "local_path": local, "remote_path": remote},
    )
    assert err and (why in text or "denied" in text or "non-interactive" in text), text
    assert not (tmp_path / "remote-up").exists() and not (workdir / ".git" / "hooks" / "pre-commit").exists()
    await pool_for(services).close()


async def test_outside_cwd_needs_read_or_edit(
    services: Services, ssh_server: SshServer, workdir: Path, tmp_path: Path
) -> None:
    on(services)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "notes.txt").write_text("private notes")
    engine, ctx = await selected(
        services, workdir, ssh_server, [Rule(permission="sftp", pattern="*", action="allow")]
    )
    up = {
        "direction": "upload",
        "local_path": str(outside / "notes.txt"),
        "remote_path": str(tmp_path / "r1"),
    }
    text, err, _ = await tool(services, ctx, engine, "env_transfer", up)
    assert err and "read" in text and not (tmp_path / "r1").exists()
    down = {
        "direction": "download",
        "local_path": str(outside / "d"),
        "remote_path": str(remote_file(tmp_path)),
    }
    text, err, _ = await tool(services, ctx, engine, "env_transfer", down)
    assert err and "edit" in text and not (outside / "d").exists()
    await pool_for(services).close()


async def test_limits_overwrite_and_symlinks(
    services: Services, ssh_server: SshServer, workdir: Path, tmp_path: Path
) -> None:
    on(services, transfer_max_bytes=10)
    engine, ctx = await selected(
        services, workdir, ssh_server, [Rule(permission="sftp", pattern="*", action="allow")]
    )
    big = remote_file(tmp_path, "big.bin", b"x" * 64)
    text, err, _ = await tool(
        services,
        ctx,
        engine,
        "env_transfer",
        {"direction": "download", "local_path": "big", "remote_path": str(big)},
    )
    assert err and "limit" in text and not (workdir / "big").exists()
    assert not leftovers(workdir)  # temp removed
    done = (await audit_rows(services, "environment.transfer_done"))[-1].detail
    assert done["ok"] is False and done["error"] == "ToolError"
    (workdir / "big.txt").write_bytes(b"y" * 64)
    text, err, _ = await tool(
        services,
        ctx,
        engine,
        "env_transfer",
        {"direction": "upload", "local_path": "big.txt", "remote_path": str(tmp_path / "r2")},
    )
    assert err and "limit" in text and not (tmp_path / "r2").exists()
    services.settings.m7.ssh.transfer_max_bytes = 1 << 20
    small = remote_file(tmp_path, "small.txt", b"new")
    (workdir / "small.txt").write_bytes(b"old")
    args = {"direction": "download", "local_path": "small.txt", "remote_path": str(small)}
    text, err, _ = await tool(services, ctx, engine, "env_transfer", args)
    assert err and "exists" in text and (workdir / "small.txt").read_bytes() == b"old"
    text, err, _ = await tool(services, ctx, engine, "env_transfer", {**args, "overwrite": True})
    assert not err and (workdir / "small.txt").read_bytes() == b"new"
    up = {"direction": "upload", "local_path": "small.txt", "remote_path": str(small)}
    text, err, _ = await tool(services, ctx, engine, "env_transfer", up)
    assert err and "exists" in text
    os.symlink(tmp_path / "remote" / "small.txt", workdir / "link.txt")
    text, err, _ = await tool(
        services,
        ctx,
        engine,
        "env_transfer",
        {"direction": "upload", "local_path": "link.txt", "remote_path": str(tmp_path / "r3")},
    )
    assert err and "symlink" in text and not (tmp_path / "r3").exists()
    os.symlink(small, tmp_path / "remote" / "rlink")
    text, err, _ = await tool(
        services,
        ctx,
        engine,
        "env_transfer",
        {"direction": "download", "local_path": "r", "remote_path": str(tmp_path / "remote" / "rlink")},
    )
    assert err and "not a regular file" in text and not (workdir / "r").exists()
    await pool_for(services).close()


async def test_plan_mode_blocks_a_download(
    services: Services, ssh_server: SshServer, workdir: Path, tmp_path: Path
) -> None:
    on(services)
    engine, ctx = await selected(
        services, workdir, ssh_server, [Rule(permission="sftp", pattern="*", action="allow")]
    )
    ctx.state.plan_mode = True
    text, err, _ = await tool(
        services,
        ctx,
        engine,
        "env_transfer",
        {"direction": "download", "local_path": "p", "remote_path": str(remote_file(tmp_path))},
    )
    assert err and "plan mode" in text and not (workdir / "p").exists()
    await pool_for(services).close()


def test_host_key_is_in_scope_only_for_environment_tools(services: Services) -> None:
    from auoreo.tools.gates import secret_names

    services.settings.m7.ssh.hosts = True
    services.harness.reload()
    assert "env_transfer" in services.harness.tools
    _ = KEY_NAME, secret_names  # scope: see test_env_ssh (the tool is in HOST_KEY_TOOLS)
