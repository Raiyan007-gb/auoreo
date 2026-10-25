"""Persistent shell: exec_command + write_stdin (spec L271)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from auoreo.backends import BackendStatus
from auoreo.core.permissions import PermissionEngine, Rule
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.tools.context import ToolContext
from auoreo.tools.shell_sessions import ShellRegistry, registry_for

ALLOW_BASH = [Rule(permission="bash", pattern="*", action="allow")]


async def call(
    services: Services,
    workdir: Path,
    name: str,
    args: dict,
    *,
    rules: list[Rule] | None = None,
    session: str = "s1",
) -> tuple[str, bool]:
    ctx = ToolContext(session_id=session, cwd=workdir, home=services.settings.home)
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine(ALLOW_BASH if rules is None else rules),
    )
    return out.result.content, out.result.is_error


def sid_of(text: str) -> str:
    m = re.search(r"session_id=(\w+)", text)
    assert m, text
    return m.group(1)


def test_manifests(services: Services) -> None:
    for name in ("exec_command", "write_stdin"):
        m = services.harness.tools[name].manifest
        assert m.exposure == "deferred" and m.is_destructive
    m = services.harness.tools["exec_command"].manifest
    assert (m.permission_name, m.pattern_arg, m.default_action) == ("bash", "cmd", "ask")


async def test_write_stdin_rides_on_the_exec_grant(services: Services, workdir: Path) -> None:
    """Bundled `bash * ask` + an allow rule for the exec'd command: write_stdin needs no
    second approval (the stored grant covers `ask` for `bash stdin:<id>`)."""
    rules = [
        Rule(permission="bash", pattern="*", action="ask"),
        Rule(permission="bash", pattern="cat", action="allow"),
    ]
    text, err = await call(
        services, workdir, "exec_command", {"cmd": "cat", "yield_time_ms": 100}, rules=rules
    )
    assert not err, text
    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home)
    ctx.services["engine"] = PermissionEngine(rules)
    out = await services.coordinator.runner.executor.run(
        ToolCall("c2", "write_stdin", json.dumps({"session_id": sid_of(text), "chars": "ping\n"})),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine(rules),
    )
    assert not out.result.is_error and "ping" in out.result.content
    text, err = await call(services, workdir, "exec_command", {"cmd": "sleep 1"}, rules=rules)
    assert err and "needs approval" in text  # other commands still ask
    await registry_for(services).close_session("s1")


async def test_echo_returns_output_and_exit(services: Services, workdir: Path) -> None:
    text, err = await call(services, workdir, "exec_command", {"cmd": "echo hello; pwd"})
    assert not err and "hello" in text and str(workdir) in text and text.endswith("[exit code 0]")
    text, err = await call(services, workdir, "exec_command", {"cmd": "echo boom >&2; exit 3"})
    assert err and "boom" in text and "[exit code 3]" in text


async def test_long_running_then_poll(services: Services, workdir: Path) -> None:
    text, err = await call(
        services, workdir, "exec_command", {"cmd": "echo start; sleep 1.5; echo done", "yield_time_ms": 300}
    )
    assert not err and "start" in text and "[running]" in text
    sid = sid_of(text)
    text, err = await call(
        services, workdir, "write_stdin", {"session_id": sid, "chars": "", "yield_time_ms": 5000}
    )
    assert not err and "done" in text and "start" not in text and "[exit code 0]" in text
    text, err = await call(services, workdir, "write_stdin", {"session_id": sid})
    assert err and "no running exec_command session" in text  # reaped once fully reported


async def test_interactive_read_via_stdin(services: Services, workdir: Path) -> None:
    text, _ = await call(
        services, workdir, "exec_command", {"cmd": "read x; echo got $x", "yield_time_ms": 200}
    )
    sid = sid_of(text)
    text, err = await call(
        services, workdir, "write_stdin", {"session_id": sid, "chars": "banana\n", "yield_time_ms": 3000}
    )
    assert not err and "got banana" in text and "[exit code 0]" in text


@pytest.mark.skipif(os.name != "posix", reason="pty is POSIX-only")
async def test_tty_mode(services: Services, workdir: Path) -> None:
    text, err = await call(
        services,
        workdir,
        "exec_command",
        {
            "cmd": "test -t 0 && echo is-a-tty; read -p 'name? ' n; echo hi $n",
            "tty": True,
            "yield_time_ms": 500,
        },
    )
    assert not err and "is-a-tty" in text and "name?" in text and "[running]" in text
    text, err = await call(
        services,
        workdir,
        "write_stdin",
        {"session_id": sid_of(text), "chars": "bob\n", "yield_time_ms": 3000},
    )
    assert not err and "hi bob" in text and "[exit code 0]" in text


async def test_floor_denies_rm_rf_root(services: Services, workdir: Path) -> None:
    text, err = await call(services, workdir, "exec_command", {"cmd": "rm -rf /"})
    assert err and "floor" in text


async def test_write_stdin_applies_floors_to_written_lines(services: Services, workdir: Path) -> None:
    text, _ = await call(services, workdir, "exec_command", {"cmd": "cat", "yield_time_ms": 100})
    sid = sid_of(text)
    text, err = await call(services, workdir, "write_stdin", {"session_id": sid, "chars": "rm -rf /\n"})
    assert err and "floor" in text
    await registry_for(services).close_session("s1")


async def test_write_stdin_needs_same_session_and_respects_deny(services: Services, workdir: Path) -> None:
    text, _ = await call(services, workdir, "exec_command", {"cmd": "cat", "yield_time_ms": 100})
    sid = sid_of(text)
    text, err = await call(services, workdir, "write_stdin", {"session_id": sid}, session="other")
    assert err and "no running exec_command session" in text
    deny = [*ALLOW_BASH, Rule(permission="bash", pattern="stdin:*", action="deny")]
    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home)
    ctx.services["engine"] = PermissionEngine(deny)
    out = await services.coordinator.runner.executor.run(
        ToolCall("c2", "write_stdin", json.dumps({"session_id": sid, "chars": "x\n"})),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine(deny),
    )
    assert out.result.is_error and "permission denied" in out.result.content
    await registry_for(services).close_session("s1")


async def test_env_is_scrubbed(services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    text, err = await call(
        services, workdir, "exec_command", {"cmd": "echo key=${OPENROUTER_API_KEY:-unset}; env | wc -l"}
    )
    assert not err and "key=unset" in text and "sk-or-secret" not in text


async def test_process_cap(services: Services, workdir: Path) -> None:
    services.settings.shell_max_processes = 2
    for _ in range(2):
        text, err = await call(services, workdir, "exec_command", {"cmd": "sleep 30", "yield_time_ms": 50})
        assert not err and "[running]" in text
    text, err = await call(services, workdir, "exec_command", {"cmd": "sleep 30", "yield_time_ms": 50})
    assert err and "too many live processes (2/2)" in text
    text, err = await call(services, workdir, "exec_command", {"cmd": "echo ok"}, session="s2")
    assert not err and "ok" in text  # the cap is per session
    await registry_for(services).close_session("s1")


async def test_kill_on_session_close_and_server_close(services: Services, workdir: Path) -> None:
    text, _ = await call(services, workdir, "exec_command", {"cmd": "sleep 60", "yield_time_ms": 50})
    reg = registry_for(services)
    proc = reg.get("s1", sid_of(text))
    await services.emit("s1", "session.archived", {})  # the bus watch closes the session
    for _ in range(100):
        if proc.proc.returncode is not None:
            break
        import asyncio

        await asyncio.sleep(0.02)
    assert proc.proc.returncode is not None and not reg.live("s1")
    text, _ = await call(services, workdir, "exec_command", {"cmd": "sleep 60", "yield_time_ms": 50})
    proc = reg.get("s1", sid_of(text))
    await services.close()  # ext close_all → registry.close()
    assert proc.proc.returncode is not None


async def test_idle_reaper(services: Services, workdir: Path) -> None:
    text, _ = await call(services, workdir, "exec_command", {"cmd": "sleep 60", "yield_time_ms": 50})
    reg = registry_for(services)
    reg.idle_s = 0.0
    proc = reg.get("s1", sid_of(text))
    await reg.reap_idle()
    assert proc.proc.returncode is not None and not reg.live("s1")


async def test_ring_buffer_drops_oldest(workdir: Path) -> None:
    reg = ShellRegistry(None)
    p = await reg.start(
        session_id="s", cmd="head -c 1500000 /dev/zero | tr '\\0' a", cwd=workdir, env=dict(os.environ),
        tty=False, granted="x",
    )  # fmt: skip
    text, dropped = await reg.collect(p, 10)
    assert dropped > 0 and len(text) == 1 << 20 and p.exit_code == 0
    await reg.close()


class _Enforcing:
    name = "bwrap"

    async def check(self) -> BackendStatus:
        return BackendStatus(True, "fake")

    def capabilities(self) -> dict[str, bool]:
        return {"fs_isolation": True, "net_isolation": True}


async def test_refuses_under_enforcing_sandbox(services: Services, workdir: Path) -> None:
    services._backends["sandbox"] = _Enforcing()
    text, err = await call(services, workdir, "exec_command", {"cmd": "echo hi"})
    assert err and "unsandboxed" in text and "`bash` tool" in text
    text, err = await call(services, workdir, "write_stdin", {"session_id": "abc"})
    assert err and "unsandboxed" in text


async def test_workdir_outside_cwd_needs_permission(
    services: Services, workdir: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    text, err = await call(services, workdir, "exec_command", {"cmd": "pwd", "workdir": str(outside)})
    assert not err  # no engine in ctx.services → secondary check is skipped (executor-only run)
    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home)
    ctx.services["engine"] = PermissionEngine(ALLOW_BASH)
    out = await services.coordinator.runner.executor.run(
        ToolCall("c3", "exec_command", json.dumps({"cmd": "pwd", "workdir": str(outside)})),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine(ALLOW_BASH),
    )
    assert out.result.is_error and "needs approval" in out.result.content
    text, err = await call(services, workdir, "exec_command", {"cmd": "pwd", "workdir": "sub"})
    assert err and "not a directory" in text
