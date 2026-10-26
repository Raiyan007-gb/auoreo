"""H1b: persistent shells (`exec_command`, `terminal_pty`, monitor) spawn inside the sandbox
backend instead of refusing under the auto (bwrap) default — spec §19.8."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from auoreo.backends import BackendStatus
from auoreo.backends.sandbox import ExecSpec
from auoreo.core import sandbox_boot
from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.tools.context import ToolContext
from auoreo.tools.shell_sessions import ShellError, sandbox_refusal, sandboxed_argv

HAS_BWRAP = sys.platform.startswith("linux") and shutil.which("bwrap") is not None
ALLOW = [
    Rule(permission="bash", pattern="*", action="allow"),
    Rule(permission="edit", pattern="*", action="allow"),
]


def backend_cls(services: Services, name: str) -> Any:
    bdef = services.harness.select_backend("sandbox", name)
    assert bdef is not None
    return bdef.load()


async def run(services: Services, workdir: Path, name: str, args: dict) -> tuple[str, bool]:  # type: ignore[type-arg]
    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home)
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine(ALLOW),
    )
    return out.result.content, out.result.is_error


async def boot_auto(services: Services) -> None:
    services.settings = Settings(home=services.settings.home, gateway="fake", backends={"sandbox": "auto"})
    boot = await sandbox_boot.start(services)
    assert boot is not None and boot.selected == "bwrap" and boot.enforced


@pytest.mark.skipif(not HAS_BWRAP, reason="needs bubblewrap on Linux")
async def test_exec_command_runs_inside_bwrap_by_default(services: Services, workdir: Path) -> None:
    await boot_auto(services)
    cmd = (
        "echo in-$((6*7)); touch made-here.txt; "
        "(touch /usr/escape 2>/dev/null && echo WROTE-USR) || echo usr-read-only; "
        "test -e /root/.bashrc -o -e /home && echo host-home-visible || echo host-home-hidden; "
        "echo ifaces=$(tail -n +3 /proc/net/dev | wc -l)"
    )
    text, err = await run(services, workdir, "exec_command", {"cmd": cmd, "yield_time_ms": 10_000})
    assert not err, text
    assert "in-42" in text and "usr-read-only" in text and "WROTE-USR" not in text
    assert "ifaces=1" in text  # net: none → only loopback in the new namespace
    assert (workdir / "made-here.txt").is_file()  # workspace-write: the cwd is writable


@pytest.mark.skipif(not HAS_BWRAP, reason="needs bubblewrap on Linux")
async def test_exec_command_interactive_session_and_stdin(services: Services, workdir: Path) -> None:
    await boot_auto(services)
    text, err = await run(
        services, workdir, "exec_command", {"cmd": "read x; echo got-$x", "yield_time_ms": 300}
    )
    assert not err and "[running]" in text, text
    sid = text.split("session_id=")[1].split()[0]
    text, err = await run(
        services, workdir, "write_stdin", {"session_id": sid, "chars": "abc\n", "yield_time_ms": 5000}
    )
    assert not err and "got-abc" in text, text


@pytest.mark.skipif(not HAS_BWRAP, reason="needs bubblewrap on Linux")
async def test_terminal_pty_runs_inside_bwrap_with_a_tty(services: Services, workdir: Path) -> None:
    await boot_auto(services)
    args = {"cmd": "echo pty-$((2+3)); tty; touch pty-made.txt", "yield_time_ms": 5000}
    text, err = await run(services, workdir, "terminal_pty", args)
    assert not err, text
    assert "pty-5" in text and "/dev/" in text and "not a tty" not in text
    assert (workdir / "pty-made.txt").is_file()


@pytest.mark.skipif(not HAS_BWRAP, reason="needs bubblewrap on Linux")
def test_bwrap_wraps_long_lived_with_the_same_profile(services: Services, tmp_path: Path) -> None:
    backend = backend_cls(services, "bwrap")(services.settings)
    spec = ExecSpec(argv=["bash", "-lc", "true"], cwd=tmp_path, mode="workspace-write", net="none")
    argv, enforced = backend.wrap_long_lived(spec, tty=True)
    assert enforced and argv[0].endswith("bwrap") and "--unshare-net" in argv
    assert "--new-session" not in argv  # keeps the private PTY as controlling terminal
    piped, _ = backend.wrap_long_lived(spec, tty=False)
    assert "--new-session" in piped
    assert ["--bind", str(tmp_path), str(tmp_path)] == piped[
        piped.index(str(tmp_path)) - 1 : piped.index(str(tmp_path)) + 2
    ]
    proxy = backend.wrap_long_lived(
        ExecSpec(argv=["x"], cwd=tmp_path, net="proxy", proxy_port=3128), tty=False
    )
    assert proxy is not None and "--unshare-net" not in proxy[0]
    no_proxy = backend.wrap_long_lived(ExecSpec(argv=["x"], cwd=tmp_path, net="proxy"), tty=False)
    assert no_proxy is not None and "--unshare-net" in no_proxy[0]  # I9: no proxy → no network


class _CannotWrap:
    name = "docker"

    async def check(self) -> BackendStatus:
        return BackendStatus(True, "fake")

    def capabilities(self) -> dict[str, bool]:
        return {"fs_isolation": True, "net_isolation": True}

    def wrap_long_lived(self, spec: ExecSpec, *, tty: bool = False) -> None:
        return None


def test_refusal_only_when_backend_cannot_wrap(tmp_path: Path) -> None:
    assert sandbox_refusal(_CannotWrap(), "exec_command") is not None
    with pytest.raises(ShellError, match="cannot confine"):
        sandboxed_argv(_CannotWrap(), "true", root=tmp_path, workdir=None, tty=False, tool="exec_command")


async def test_unwrappable_backend_still_refuses_terminal_pty(services: Services, workdir: Path) -> None:
    services._backends["sandbox"] = _CannotWrap()
    text, err = await run(services, workdir, "terminal_pty", {"cmd": "echo hi"})
    assert err and "cannot confine" in text


def test_none_backend_passes_through(services: Services, tmp_path: Path) -> None:
    backend = backend_cls(services, "none")(services.settings)
    argv, enforced = backend.wrap_long_lived(ExecSpec(argv=["bash", "-lc", "x"], cwd=tmp_path))
    assert argv == ["bash", "-lc", "x"] and not enforced
    assert sandbox_refusal(backend, "exec_command") is None
