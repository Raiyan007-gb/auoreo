"""docker environment backend against a fake Docker SDK client (no daemon here)."""

from __future__ import annotations

import json
import struct
import threading
from pathlib import Path
from typing import Any

import pytest

from auoreo.backends.environment import ExecRequest
from auoreo.core.permissions import PermissionEngine, Rule
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.tools.context import ToolContext


def real(p: Path) -> str:
    return str(p.resolve())


def frame(data: bytes, stream: int = 1) -> bytes:
    return struct.pack(">BxxxI", stream, len(data)) + data


class FakeSock:
    """Raw exec socket: scripted output, echoes stdin for `cat`, EOF when closed/finished."""

    def __init__(self, out: bytes, *, echo: bool, tty: bool) -> None:
        self.pending = out
        self.echo, self.tty = echo, tty
        self.eof = not echo
        self.closed = False
        self.cv = threading.Condition()

    def recv(self, n: int) -> bytes:
        with self.cv:
            while not self.pending and not self.eof and not self.closed:
                self.cv.wait(0.05)
            if self.closed:
                raise OSError("closed")
            data, self.pending = self.pending[:n], self.pending[n:]
            return data

    def sendall(self, data: bytes) -> None:
        with self.cv:
            self.pending += data if self.tty else frame(data)
            self.cv.notify_all()

    def close(self) -> None:
        with self.cv:
            self.closed = True
            self.cv.notify_all()


class FakeApi:
    def __init__(self) -> None:
        self.creates: list[dict[str, Any]] = []
        self.codes: dict[str, int] = {}

    def exec_create(self, container: str, cmd: list[str], **kw: Any) -> dict[str, str]:
        eid = f"e{len(self.creates)}"
        self.creates.append({"container": container, "cmd": cmd, **kw})
        self.codes[eid] = 7 if "exit 7" in cmd[-1] else 0
        return {"Id": eid}

    def exec_start(self, eid: str, socket: bool, tty: bool) -> FakeSock:
        assert socket
        cmd = self.creates[int(eid[1:])]["cmd"][-1]
        if cmd == "cat":
            return FakeSock(b"", echo=True, tty=tty)
        out = b"out\n" if tty else frame(b"out\n") + frame(b"err\n", 2)
        return FakeSock(out, echo=False, tty=tty)

    def exec_inspect(self, eid: str) -> dict[str, int]:
        return {"ExitCode": self.codes[eid]}


class FakeContainer:
    id = "c0ffee"

    def __init__(self) -> None:
        self.removed = False

    def remove(self, force: bool = False) -> None:
        self.removed = force


class FakeContainers:
    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []
        self.made: list[FakeContainer] = []

    def run(self, **kw: Any) -> FakeContainer:
        self.runs.append(kw)
        self.made.append(FakeContainer())
        return self.made[-1]


class FakeClient:
    def __init__(self, fail: bool = False) -> None:
        self.api = FakeApi()
        self.containers = FakeContainers()
        self.fail = fail

    def ping(self) -> bool:
        if self.fail:
            raise ConnectionError("no socket")
        return True

    def close(self) -> None:
        return None


@pytest.fixture
def docker_cls(services: Services, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, FakeClient]:
    bdef = services.harness.select_backend("environment", "docker")
    assert bdef is not None
    cls = bdef.load()
    client = FakeClient()
    monkeypatch.setattr(cls, "client_factory", staticmethod(lambda **kw: client))
    return cls, client


async def test_container_hardening_and_exec(services: Services, workdir: Path, docker_cls: Any) -> None:
    cls, client = docker_cls
    b = cls(services.settings)
    assert b.remote is False and (await b.check()).ok
    b.prepare_session("s1", workdir)
    chunk = await b.exec_command(
        ExecRequest("echo out; echo err >&2; exit 7", workdir="sub"), session_id="s1"
    )
    assert chunk.exit_code == 7 and "out" in chunk.output and "err" in chunk.output
    (kw,) = client.containers.runs
    assert kw["cap_drop"] == ["ALL"] and kw["security_opt"] == ["no-new-privileges"]
    assert kw["user"] == "1000:1000" and kw["network_mode"] == "none"
    assert "privileged" not in kw and kw["image"] == "python:3.12-slim"
    assert kw["volumes"] == {str(workdir): {"bind": "/work", "mode": "rw"}}
    assert kw["labels"]["auoreo.session"] == "s1"
    ex = client.api.creates[0]
    assert ex["cmd"][:2] == ["bash", "-lc"] and ex["user"] == "1000:1000" and ex["workdir"] == "/work/sub"
    # second command reuses the long-lived container
    await b.exec_command(ExecRequest("true", env={"PATH": "/host/bin", "LANG": "C"}), session_id="s1")
    assert len(client.containers.runs) == 1 and client.api.creates[1]["environment"] == {"LANG": "C"}
    await b.close_session("s1")
    assert client.containers.made[0].removed


async def test_network_setting_and_interactive_stdin(services: Services, docker_cls: Any) -> None:
    cls, client = docker_cls
    services.settings.environment_docker_network = "bridge"
    services.settings.environment_docker_image = "debian:13-slim"
    b = cls(services.settings)
    chunk = await b.exec_command(ExecRequest("cat", yield_time_ms=50), session_id="s1")
    assert chunk.exit_code is None and chunk.process_id
    assert client.containers.runs[0]["network_mode"] == "bridge"
    assert client.containers.runs[0]["image"] == "debian:13-slim"
    chunk = await b.write_stdin(chunk.process_id, "ping\n", yield_time_ms=500)
    assert "ping" in chunk.output and chunk.exit_code is None
    tty = await b.exec_command(ExecRequest("echo out", tty=True), session_id="s1")
    assert tty.exit_code == 0 and tty.output == "out\n" and client.api.creates[-1]["tty"] is True
    await b.close()
    assert client.containers.made[0].removed


async def test_check_reports_unreachable_daemon(services: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    bdef = services.harness.select_backend("environment", "docker")
    assert bdef is not None
    cls = bdef.load()
    monkeypatch.setattr(cls, "client_factory", staticmethod(lambda **kw: FakeClient(fail=True)))
    status = await cls(services.settings).check()
    assert not status.ok and "unreachable" in status.detail and status.remedy

    def missing(**kw: Any) -> Any:
        raise ImportError("docker")

    monkeypatch.setattr(cls, "client_factory", staticmethod(missing))
    status = await cls(services.settings).check()
    assert not status.ok and "not installed" in status.detail


async def test_tool_path_uses_selected_docker_backend(
    services: Services, workdir: Path, docker_cls: Any
) -> None:
    _, client = docker_cls
    services.settings.backends["environment"] = "docker"
    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home)
    out = await services.coordinator.runner.executor.run(
        ToolCall("c", "exec_command", json.dumps({"cmd": "echo out"})),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine([Rule(permission="bash", pattern="*", action="allow")]),
    )
    assert not out.result.is_error and "out" in out.result.content
    assert out.result.meta["environment"] == "docker" and out.result.meta["remote"] is False
    assert client.containers.runs[0]["volumes"] == {real(workdir): {"bind": "/work", "mode": "rw"}}
    await services.close_backend_session("s1")  # session delete path removes the container
    assert client.containers.made[0].removed
