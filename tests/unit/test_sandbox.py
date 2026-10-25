"""Sandbox backends (spec §19.8): none / bwrap / docker, the startup policy, the one-time note."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from auoreo.backends.sandbox import ExecSpec, SandboxError
from auoreo.core import sandbox_boot
from auoreo.core.db import AuditRow
from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.tools.context import ToolContext

ALLOW = [Rule(permission="*", pattern="*", action="allow")]


def backend_cls(services: Services, name: str) -> Any:
    bdef = services.harness.select_backend("sandbox", name)
    assert bdef is not None
    return bdef.load()


async def audit_rows(services: Services, action: str) -> list[AuditRow]:
    async with services.db.session() as s:
        return list((await s.execute(select(AuditRow).where(AuditRow.action == action))).scalars())


async def bash(services: Services, ctx: ToolContext, command: str, **extra: Any) -> tuple[str, bool]:
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", "bash", json.dumps({"command": command, **extra})),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine(ALLOW),
    )
    return out.result.content, out.result.is_error


# ------------------------------------------------------------------ none
async def test_none_backend_runs_and_is_not_enforced(services: Services, workdir: Path) -> None:
    sb = backend_cls(services, "none")(services.settings)
    assert sb.capabilities() == {"fs_isolation": False, "net_isolation": False}
    assert (await sb.check()).ok
    res = await sb.run(ExecSpec(argv=["bash", "-c", "echo out; echo err >&2; exit 3"], cwd=workdir))
    assert (res.exit_code, res.stdout, res.stderr) == (3, "out\n", "err\n")
    assert res.enforced is False and res.backend == "none" and not res.timed_out


async def test_none_backend_kills_the_process_group_on_timeout(services: Services, workdir: Path) -> None:
    sb = backend_cls(services, "none")(services.settings)
    marker = workdir / "survived"
    started = time.monotonic()
    res = await sb.run(
        ExecSpec(argv=["bash", "-c", f"(sleep 1; touch {marker}) & sleep 30"], cwd=workdir, timeout_s=0.3)
    )
    assert res.timed_out and res.exit_code == -9
    assert time.monotonic() - started < 5
    await asyncio.sleep(1.3)
    assert not marker.exists()  # the background child died with the group


async def test_not_enforced_note_appears_once_per_session(services: Services, workdir: Path) -> None:
    assert services.backend("sandbox").name == "none"
    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home)
    first, err = await bash(services, ctx, "echo hi")
    assert not err and "hi" in first and "[exit code 0]" in first
    assert first.rstrip().endswith("[sandbox: not enforced — backend 'none']")
    second, _ = await bash(services, ctx, "echo again")
    assert "not enforced" not in second
    other = ToolContext(session_id="s2", cwd=workdir, home=services.settings.home)
    third, _ = await bash(services, other, "echo new session")
    assert "not enforced" in third


async def test_bash_audit_records_net(services: Services, workdir: Path) -> None:
    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home)
    await bash(services, ctx, "true")
    rows = await audit_rows(services, "tool.call")
    assert rows[-1].detail["net"] == "none"


# ------------------------------------------------------------------ bwrap
@pytest.mark.parametrize("mode", ["read-only", "workspace-write", "danger-full-access"])
@pytest.mark.parametrize("net", ["none", "proxy", "direct"])
async def test_bwrap_argv(services: Services, workdir: Path, mode: str, net: str) -> None:
    impl = sys.modules[backend_cls(services, "bwrap").__module__]
    ro, rw = Path("/opt/data"), workdir / "out"
    spec = ExecSpec(
        argv=["bash", "-c", "ls"],
        cwd=workdir,
        mode=mode,
        net=net,
        readable=[ro],
        writable=[rw],  # type: ignore[arg-type]
    )
    argv = impl.build_argv(spec, "/usr/bin/bwrap")
    assert argv[:4] == ["/usr/bin/bwrap", "--ro-bind", "/usr", "/usr"]
    for flag in ("--unshare-pid", "--unshare-user-try", "--die-with-parent", "--new-session"):
        assert flag in argv
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert ("--unshare-net" in argv) == (net == "none")
    assert "--proc" in argv and "--dev" in argv
    cwd = str(workdir)
    bind = "--ro-bind" if mode == "read-only" else "--bind"
    assert any(argv[i : i + 3] == [bind, cwd, cwd] for i in range(len(argv)))
    assert any(argv[i : i + 3] == ["--ro-bind", str(ro), str(ro)] for i in range(len(argv)))
    has_rw = any(argv[i : i + 3] == ["--bind", str(rw), str(rw)] for i in range(len(argv)))
    assert has_rw == (mode != "read-only")
    assert argv[-6:] == ["--chdir", cwd, "--", "bash", "-c", "ls"]


@pytest.mark.skipif(shutil.which("bwrap") is not None, reason="bwrap is installed here")
async def test_bwrap_check_is_honest_when_missing(services: Services) -> None:
    status = await backend_cls(services, "bwrap")(services.settings).check()
    assert not status.ok and status.detail == "bwrap not installed" and "bubblewrap" in status.remedy


async def test_mount_outside_cwd_needs_full_access(services: Services, workdir: Path) -> None:
    sb = backend_cls(services, "bwrap")(services.settings)
    spec = ExecSpec(argv=["true"], cwd=workdir, readable=[Path("/etc")])
    with pytest.raises(SandboxError, match="danger-full-access"):
        await sb.run(spec)


# ------------------------------------------------------------------ docker (fake SDK)
class FakeContainer:
    def __init__(self, wait_s: float = 0.0, code: int = 0) -> None:
        self.wait_s, self.code = wait_s, code
        self.killed = self.removed = False

    def wait(self, timeout: float) -> dict[str, int]:
        if self.wait_s > timeout:
            time.sleep(timeout)
            raise TimeoutError("read timed out")
        return {"StatusCode": self.code}

    def logs(self, stdout: bool = True, stderr: bool = True) -> bytes:
        return (b"hi\n" if stdout else b"") + (b"warn\n" if stderr else b"")

    def kill(self) -> None:
        self.killed = True

    def remove(self, force: bool = False) -> None:
        self.removed = True


class FakeClient:
    def __init__(self, container: FakeContainer) -> None:
        self.container = container
        self.calls: list[dict[str, Any]] = []
        self.containers = self

    def run(self, **kwargs: Any) -> FakeContainer:
        self.calls.append(kwargs)
        return self.container

    def ping(self) -> bool:
        return True


def docker_with(services: Services, monkeypatch: pytest.MonkeyPatch, client: Any) -> Any:
    cls = backend_cls(services, "docker")
    monkeypatch.setattr(cls, "client_factory", staticmethod(lambda **kw: client))
    return cls(services.settings)


@pytest.mark.parametrize(
    ("mode", "net", "network", "ro"),
    [
        ("read-only", "none", "none", True),
        ("workspace-write", "none", "none", False),
        ("workspace-write", "proxy", "bridge", False),
        ("danger-full-access", "direct", "bridge", False),
    ],
)
async def test_docker_run_kwargs(
    services: Services,
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    net: str,
    network: str,
    ro: bool,
) -> None:
    client = FakeClient(FakeContainer())
    sb = docker_with(services, monkeypatch, client)
    spec = ExecSpec(argv=["echo", "hi"], cwd=workdir, mode=mode, net=net, env={"PATH": "/host", "X": "1"})  # type: ignore[arg-type]
    res = await sb.run(spec)
    assert (res.exit_code, res.stdout, res.enforced, res.backend) == (0, "hi\n", True, "docker")
    kw = client.calls[0]
    assert "privileged" not in kw
    assert kw["cap_drop"] == ["ALL"] and kw["security_opt"] == ["no-new-privileges"]
    assert kw["network_mode"] == network and kw["read_only"] is ro
    assert kw["volumes"] == {str(workdir): {"bind": "/work", "mode": "ro" if ro else "rw"}}
    assert kw["working_dir"] == "/work" and kw["user"] == "1000:1000" and kw["pids_limit"] == 256
    assert kw["image"] == "python:3.12-slim" and kw["mem_limit"] == "1g" and kw["detach"] is True
    assert kw["environment"] == {"X": "1"}  # host PATH never leaks into the container
    assert client.container.removed


async def test_docker_timeout_kills_container(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient(FakeContainer(wait_s=10))
    sb = docker_with(services, monkeypatch, client)
    res = await sb.run(ExecSpec(argv=["sleep", "10"], cwd=workdir, timeout_s=0.2))
    assert res.timed_out and client.container.killed and client.container.removed


async def test_docker_host_mount_needs_full_access_and_is_audited(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = FakeClient(FakeContainer())
    sb = docker_with(services, monkeypatch, client)
    seen: list[tuple[str, dict[str, Any]]] = []

    async def audit(action: str, detail: dict[str, Any]) -> None:
        seen.append((action, detail))

    sb.audit = audit
    extra = tmp_path / "shared"
    extra.mkdir()
    with pytest.raises(SandboxError):
        await sb.run(ExecSpec(argv=["ls"], cwd=workdir, writable=[extra]))
    assert not client.calls
    await sb.run(ExecSpec(argv=["ls"], cwd=workdir, writable=[extra], mode="danger-full-access"))
    assert client.calls[0]["volumes"][str(extra)] == {"bind": str(extra), "mode": "rw"}
    assert "privileged" not in client.calls[0]
    assert seen == [("sandbox.mount", {"backend": "docker", "args": [str(extra)]})]


async def test_docker_check_reports_an_unreachable_daemon(
    services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(**kw: Any) -> Any:
        raise ConnectionError("Error while fetching server API version")

    cls = backend_cls(services, "docker")
    monkeypatch.setattr(cls, "client_factory", staticmethod(boom))
    status = await cls(services.settings).check()
    assert not status.ok and "unreachable" in status.detail and status.remedy


def _docker_up() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(not _docker_up(), reason="no reachable Docker daemon")
async def test_docker_real_run(services: Services, workdir: Path) -> None:
    sb = backend_cls(services, "docker")(services.settings)
    assert (await sb.check()).ok
    res = await sb.run(ExecSpec(argv=["sh", "-c", "echo hi; touch /work/x"], cwd=workdir, mode="read-only"))
    assert res.enforced and "hi" in res.stdout and res.exit_code != 0
    assert res.ro_denied(ExecSpec(argv=[], cwd=workdir, mode="read-only"))


# ------------------------------------------------------------------ startup policy
def _fail_docker(services: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**kw: Any) -> Any:
        raise ConnectionError("no daemon")

    monkeypatch.setattr(backend_cls(services, "docker"), "client_factory", staticmethod(boom))


async def test_policy_warn_falls_back_to_none_and_audits(
    services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    services.settings = Settings(home=services.settings.home, gateway="fake", backends={"sandbox": "docker"})
    _fail_docker(services, monkeypatch)
    boot = await sandbox_boot.start(services)
    assert boot is not None and boot.fell_back and boot.selected == "docker"
    assert services.backend("sandbox").name == "none"
    assert boot.describe()["enforced"] is False
    rows = await audit_rows(services, "sandbox.fallback")
    assert rows and rows[0].detail["selected"] == "docker" and rows[0].detail["fallback"] == "none"


async def test_policy_fail_refuses_to_start(services: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    services.settings = Settings(
        home=services.settings.home, gateway="fake", backends={"sandbox": "docker"}, sandbox_policy="fail"
    )
    _fail_docker(services, monkeypatch)
    with pytest.raises(sandbox_boot.SandboxStartupRefused, match="refusing to start"):
        await sandbox_boot.start(services)


async def test_default_boot_uses_none(services: Services) -> None:
    boot = services.ext["sandbox_boot"]
    assert boot.selected == "none" and not boot.fell_back and boot.status.ok
    assert services.backend("sandbox") is boot.backend
