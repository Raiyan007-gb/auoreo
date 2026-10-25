"""G4b: terminal_pty family (start/send/read/signal/close/list) + background jobs tools."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from auoreo.core.permissions import PermissionEngine, Rule
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.tools.context import ToolContext
from auoreo.tools.pty_jobs import KEYS, keys_to_chars, pty_registry, strip_ansi
from auoreo.tools.shell_sessions import registry_for

ALLOW_BASH = [Rule(permission="bash", pattern="*", action="allow")]
PTY_TOOLS = ["terminal_pty_send", "terminal_pty_read", "terminal_pty_signal", "terminal_pty_close"]


async def call(
    services: Services, workdir: Path, name: str, args: dict, *, session: str = "s1"
) -> tuple[str, bool]:
    ctx = ToolContext(session_id=session, cwd=workdir, home=services.settings.home)
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine(ALLOW_BASH),
    )
    return out.result.content, out.result.is_error


def sid_of(text: str) -> str:
    m = re.search(r"session_id=(\w+)", text)
    assert m, text
    return m.group(1)


async def until_output(services: Services, workdir: Path, sid: str, needle: str, seen: str = "") -> str:
    for _ in range(20):
        text, err = await call(
            services, workdir, "terminal_pty_read", {"session_id": sid, "yield_time_ms": 300}
        )
        assert not err, text
        seen += text
        if needle in seen:
            return seen
    raise AssertionError(f"{needle!r} not in {seen!r}")


def test_manifests(services: Services) -> None:
    tools = services.harness.tools
    start = tools["terminal_pty"].manifest
    assert (start.permission_name, start.pattern_arg, start.default_action) == ("bash", "cmd", "ask")
    assert start.exposure == "deferred" and start.interrupt == "cancel"
    for name in [*PTY_TOOLS, "terminal_pty_list"]:
        m = tools[name].manifest
        assert (m.permission_name, m.default_action, m.toolset) == ("terminal_pty", "allow", "shell")
    for name in ("jobs_list", "jobs_output", "jobs_kill"):
        assert tools[name].manifest.permission_name == "jobs"
    assert tools["terminal_pty_list"].manifest.is_read_only and tools["jobs_list"].manifest.is_read_only


def test_keys_and_ansi() -> None:
    assert keys_to_chars(["ctrl-c", "enter", "up"]) == "\x03\r\x1b[A"
    assert set(KEYS) >= {"enter", "tab", "esc", "ctrl-d"}
    assert strip_ansi("\x1b[1;32mok\x1b[0m\r\n\x1b]0;title\x07x") == "ok\nx"


async def test_pty_round_trip_signal_and_close(services: Services, workdir: Path) -> None:
    text, err = await call(services, workdir, "terminal_pty", {"cmd": "cat", "yield_time_ms": 200})
    assert not err and "[running]" in text, text
    sid = sid_of(text)

    text, err = await call(
        services,
        workdir,
        "terminal_pty_send",
        {"session_id": sid, "chars": "hello-pty\n", "yield_time_ms": 500},
    )
    assert not err and "hello-pty" in text

    text, err = await call(services, workdir, "terminal_pty_read", {"session_id": sid, "tail_chars": 200})
    assert not err and "hello-pty" in text  # non-consuming tail includes already-read output

    text, err = await call(services, workdir, "terminal_pty_list", {})
    assert not err and f"| {sid} |" in text and "`cat`" in text
    text, err = await call(services, workdir, "jobs_list", {})
    assert f"| {sid} | pty |" in text

    # another AUOREO session cannot see or drive this PTY
    text, err = await call(
        services, workdir, "terminal_pty_send", {"session_id": sid, "chars": "x"}, session="s2"
    )
    assert err and "no terminal_pty session" in text

    text, err = await call(services, workdir, "terminal_pty_signal", {"session_id": sid, "signal": "INT"})
    assert not err and "sent SIGINT" in text
    if "[exit code" not in text:
        text = await until_output(services, workdir, sid, "[exit code", text)
    assert "[exit code" in text
    text, err = await call(services, workdir, "terminal_pty_list", {})
    assert "no open terminal_pty sessions" in text


async def test_pty_interactive_shell_and_close(services: Services, workdir: Path) -> None:
    (workdir / "marker.txt").write_text("x")
    text, err = await call(
        services, workdir, "terminal_pty", {"cmd": "bash --noprofile --norc -i", "yield_time_ms": 300}
    )
    assert not err, text
    sid = sid_of(text)
    args = {"session_id": sid, "chars": "ls; echo $TERM", "keys": ["enter"]}
    first, _ = await call(services, workdir, "terminal_pty_send", args)
    seen = await until_output(services, workdir, sid, "dumb", first)
    assert "marker.txt" in seen
    text, err = await call(services, workdir, "terminal_pty_close", {"session_id": sid})
    assert not err and f"[closed] {sid}" in text
    assert pty_registry(services).live("s1") == {}
    text, err = await call(services, workdir, "terminal_pty_read", {"session_id": sid})
    assert err and "no terminal_pty session" in text


async def test_pty_send_applies_floors(services: Services, workdir: Path) -> None:
    text, _ = await call(services, workdir, "terminal_pty", {"cmd": "cat", "yield_time_ms": 50})
    sid = sid_of(text)
    text, err = await call(services, workdir, "terminal_pty_send", {"session_id": sid, "chars": "rm -rf /\n"})
    assert err and "floor" in text
    await pty_registry(services).close_session("s1")


async def test_pty_cap(services: Services, workdir: Path) -> None:
    services.settings.tools.pty_max_sessions = 1
    text, err = await call(services, workdir, "terminal_pty", {"cmd": "cat", "yield_time_ms": 50})
    assert not err
    text, err = await call(services, workdir, "terminal_pty", {"cmd": "cat", "yield_time_ms": 50})
    assert err and "too many live processes" in text
    await pty_registry(services).close_session("s1")


async def test_pty_closed_with_the_session(services: Services, workdir: Path) -> None:
    text, _ = await call(services, workdir, "terminal_pty", {"cmd": "sleep 30", "yield_time_ms": 50})
    assert pty_registry(services).live("s1")
    await services.ext["pty_sessions"].close_session("s1")
    assert pty_registry(services).live("s1") == {}


async def test_jobs_output_list_kill(services: Services, workdir: Path) -> None:
    text, err = await call(services, workdir, "jobs_list", {})
    assert not err and text == "no background jobs"

    text, err = await call(
        services, workdir, "exec_command", {"cmd": "sleep 0.3; echo job-done", "yield_time_ms": 0}
    )
    assert not err and "[running]" in text, text
    quick = sid_of(text)
    text, err = await call(services, workdir, "exec_command", {"cmd": "sleep 30", "yield_time_ms": 0})
    slow = sid_of(text)

    text, err = await call(services, workdir, "jobs_list", {})
    assert f"| {quick} | exec | local |" in text and f"| {slow} | exec |" in text and "running" in text

    text, err = await call(services, workdir, "jobs_output", {"job_id": quick, "yield_time_ms": 5000})
    assert not err and "job-done" in text and "[exit code 0]" in text

    text, err = await call(services, workdir, "jobs_kill", {"job_id": slow})
    assert not err and f"killed exec job {slow}" in text
    assert registry_for(services).find(slow) is None
    text, err = await call(services, workdir, "jobs_list", {})
    assert text == "no background jobs"

    text, err = await call(services, workdir, "jobs_output", {"job_id": "nope"})
    assert err and "no background job 'nope'" in text


async def test_jobs_kill_pty(services: Services, workdir: Path) -> None:
    text, _ = await call(services, workdir, "terminal_pty", {"cmd": "sleep 30", "yield_time_ms": 50})
    sid = sid_of(text)
    text, err = await call(services, workdir, "jobs_output", {"job_id": sid, "yield_time_ms": 50})
    assert not err and "[running]" in text
    text, err = await call(services, workdir, "jobs_kill", {"job_id": sid})
    assert not err and "killed pty job" in text
    await asyncio.sleep(0)
    assert pty_registry(services).live("s1") == {}


@pytest.mark.parametrize("name", PTY_TOOLS)
async def test_unknown_pty_session(services: Services, workdir: Path, name: str) -> None:
    text, err = await call(services, workdir, name, {"session_id": "zzz"})
    assert err and "no terminal_pty session 'zzz'" in text
