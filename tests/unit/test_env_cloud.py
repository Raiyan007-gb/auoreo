"""Contract tests for the cloud environment backends (no live accounts here).

E2B and Modal run against fake SDK modules injected into `sys.modules` (their process
streams are SDK-only); E2B's `check()` and all of Daytona run against `pytest_httpx`.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from auoreo.backends.environment import ExecRequest
from auoreo.core.services import Services
from auoreo.tools.shell_sessions import ShellError


def cls_of(services: Services, name: str) -> Any:
    bdef = services.harness.select_backend("environment", name)
    assert bdef is not None
    return bdef.load()


class Aio:
    """`modal`-style callable with an `.aio` coroutine variant."""

    def __init__(self, fn: Any) -> None:
        self.aio = fn


# ------------------------------------------------------------------ E2B
class FakeE2BHandle:
    def __init__(self, cmd: str, on_stdout: Any, on_stderr: Any) -> None:
        self.pid = 42
        self.cmd = cmd
        self.on_stdout, self.on_stderr = on_stdout, on_stderr
        self.stdin: asyncio.Queue[str] = asyncio.Queue()
        self.killed = False

    async def wait(self) -> Any:
        if self.cmd.startswith("read"):
            line = await self.stdin.get()
            self.on_stdout(f"got:{line}")
            return types.SimpleNamespace(exit_code=0)
        self.on_stdout("out\n")
        self.on_stderr("err\n")
        exc = RuntimeError("exit")
        exc.exit_code = 5  # type: ignore[attr-defined]  # CommandExitException shape
        raise exc

    async def kill(self) -> bool:
        self.killed = True
        return True


class FakeE2BCommands:
    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []
        self.handles: list[FakeE2BHandle] = []

    async def run(self, cmd: str, **kw: Any) -> FakeE2BHandle:
        self.runs.append({"cmd": cmd, **kw})
        self.handles.append(FakeE2BHandle(cmd, kw["on_stdout"], kw["on_stderr"]))
        return self.handles[-1]

    async def send_stdin(self, pid: int, data: str) -> None:
        assert pid == 42
        await self.handles[-1].stdin.put(data)


class FakeE2BSandbox:
    created: list[dict[str, Any]] = []

    def __init__(self) -> None:
        self.commands = FakeE2BCommands()
        self.killed = False

    @classmethod
    async def create(cls, **kw: Any) -> FakeE2BSandbox:
        cls.created.append(kw)
        return cls()

    async def kill(self) -> bool:
        self.killed = True
        return True


@pytest.fixture
def fake_e2b(monkeypatch: pytest.MonkeyPatch) -> type[FakeE2BSandbox]:
    FakeE2BSandbox.created = []
    monkeypatch.setitem(sys.modules, "e2b", types.SimpleNamespace(AsyncSandbox=FakeE2BSandbox))
    return FakeE2BSandbox


async def test_e2b_lifecycle_exec_and_stdin(
    services: Services, fake_e2b: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("E2B_API_KEY", "e2b-key")
    b = cls_of(services, "e2b")(services.settings)
    assert b.remote is True
    chunk = await b.exec_command(ExecRequest("make test", workdir="/code"), session_id="s1")
    assert chunk.exit_code == 5 and "out" in chunk.output and "err" in chunk.output
    (created,) = fake_e2b.created
    assert created["api_key"] == "e2b-key" and created["template"] == "base" and created["timeout"] == 3600
    sbx = b._sandboxes["s1"]
    run = sbx.commands.runs[0]
    assert (
        run["background"] is True and run["stdin"] is True and run["cwd"] == "/code" and run["timeout"] == 0
    )
    chunk = await b.exec_command(ExecRequest("read x", yield_time_ms=50), session_id="s1")
    assert chunk.exit_code is None and chunk.process_id
    chunk = await b.write_stdin(chunk.process_id, "hi\n", yield_time_ms=2000)
    assert chunk.exit_code == 0 and "got:hi" in chunk.output
    assert len(fake_e2b.created) == 1  # one sandbox per session, created lazily
    await b.close_session("s1")
    assert sbx.killed and "s1" not in b._sandboxes


async def test_e2b_needs_key_and_reports_reachability(
    services: Services, fake_e2b: Any, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = cls_of(services, "e2b")(services.settings)
    status = await b.check()
    assert not status.ok and "E2B_API_KEY" in status.detail
    with pytest.raises(ShellError, match="E2B_API_KEY is not set"):
        await b.exec_command(ExecRequest("true"), session_id="s1")
    monkeypatch.setenv("E2B_API_KEY", "e2b-key")
    httpx_mock.add_response(
        method="GET", url="https://api.e2b.app/sandboxes", match_headers={"X-API-Key": "e2b-key"}, json=[]
    )
    assert (await b.check()).ok
    httpx_mock.add_response(method="GET", url="https://api.e2b.app/sandboxes", status_code=401, json={})
    status = await b.check()
    assert not status.ok and "rejected" in status.detail and "e2b-key" not in status.detail
    httpx_mock.add_exception(httpx.ConnectError("down"), url="https://api.e2b.app/sandboxes")
    status = await b.check()
    assert not status.ok and "unreachable" in status.detail


# ------------------------------------------------------------------ Modal
class FakeStream:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks

    def __aiter__(self) -> Any:
        async def gen() -> Any:
            for c in self.chunks:
                yield c

        return gen()


class FakeModalProc:
    def __init__(self, argv: tuple[str, ...]) -> None:
        self.argv = argv
        self.stdout = FakeStream(["hello\n"])
        self.stderr = FakeStream(["warn\n"])
        self.written = b""

        async def drain() -> None:
            return None

        async def wait() -> int:
            return 3

        self.stdin = types.SimpleNamespace(write=self._write, drain=Aio(drain))
        self.wait = Aio(wait)

    def _write(self, data: bytes) -> None:
        self.written += data


def fake_modal_module(log: dict[str, Any]) -> types.SimpleNamespace:
    class Sandbox:
        def __init__(self) -> None:
            self.terminated = False

            async def exec_(*argv: str, **kw: Any) -> FakeModalProc:
                log.setdefault("exec", []).append((argv, kw))
                return FakeModalProc(argv)

            async def terminate() -> None:
                self.terminated = True

            self.exec = Aio(exec_)
            self.terminate = Aio(terminate)

    async def create(**kw: Any) -> Sandbox:
        log["create"] = kw
        log["sandbox"] = Sandbox()
        return log["sandbox"]

    async def lookup(name: str, **kw: Any) -> str:
        log["lookup"] = (name, kw)
        return f"app:{name}"

    def from_credentials(token_id: str, token_secret: str) -> str:
        log["creds"] = (token_id, token_secret)
        return "client"

    return types.SimpleNamespace(
        Client=types.SimpleNamespace(from_credentials=from_credentials),
        App=types.SimpleNamespace(lookup=Aio(lookup)),
        Image=types.SimpleNamespace(from_registry=lambda ref: f"image:{ref}"),
        Sandbox=types.SimpleNamespace(create=Aio(create)),
    )


async def test_modal_contract(services: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    log: dict[str, Any] = {}
    monkeypatch.setitem(sys.modules, "modal", fake_modal_module(log))
    b = cls_of(services, "modal")(services.settings)
    status = await b.check()
    assert not status.ok and "MODAL_TOKEN_ID" in status.detail
    monkeypatch.setenv("MODAL_TOKEN_ID", "ak-1")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "as-1")
    status = await b.check()
    assert status.ok and "not contacted" in status.detail
    chunk = await b.exec_command(ExecRequest("python -V", workdir="/root"), session_id="s1")
    assert chunk.exit_code == 3 and "hello" in chunk.output and "warn" in chunk.output
    assert log["creds"] == ("ak-1", "as-1")
    assert log["lookup"] == ("auoreo-environments", {"create_if_missing": True, "client": "client"})
    assert log["create"] == {
        "app": "app:auoreo-environments",
        "image": "image:python:3.12-slim",
        "timeout": 3600,
        "client": "client",
    }
    assert log["exec"][0] == (("bash", "-lc", "python -V"), {"workdir": "/root"})
    await b.close_session("s1")
    assert log["sandbox"].terminated


# ------------------------------------------------------------------ Daytona
API = "https://app.daytona.io/api"


async def test_daytona_contract(
    services: Services, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = cls_of(services, "daytona")(services.settings)
    assert not (await b.check()).ok
    monkeypatch.setenv("DAYTONA_API_KEY", "dt-key")
    auth = {"Authorization": "Bearer dt-key"}
    httpx_mock.add_response(method="GET", url=f"{API}/sandbox", match_headers=auth, json=[])
    assert (await b.check()).ok
    httpx_mock.add_response(
        method="POST", url=f"{API}/sandbox", match_headers=auth, json={"id": "sb1", "state": "creating"}
    )
    httpx_mock.add_response(method="GET", url=f"{API}/sandbox/sb1", json={"id": "sb1", "state": "started"})
    httpx_mock.add_response(
        method="POST",
        url=f"{API}/toolbox/sb1/toolbox/process/execute",
        match_headers=auth,
        match_json={"command": "bash -lc 'echo hi'", "timeout": 3600, "cwd": "/home/daytona"},
        json={"exitCode": 0, "result": "hi\n"},
    )
    b.poll_s = 0.01
    chunk = await b.exec_command(ExecRequest("echo hi", workdir="/home/daytona"), session_id="s1")
    assert chunk.exit_code == 0 and chunk.output == "hi\n"
    create = json.loads(httpx_mock.get_requests(method="POST", url=f"{API}/sandbox")[0].content)
    assert create["labels"] == {"auoreo_session": "s1"} and create["autoStopInterval"] == 60

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.3)
        return httpx.Response(200, json={"exitCode": 1, "result": "late\n"})

    httpx_mock.add_callback(slow, method="POST", url=f"{API}/toolbox/sb1/toolbox/process/execute")
    chunk = await b.exec_command(ExecRequest("sleep 1", yield_time_ms=20), session_id="s1")
    assert chunk.exit_code is None and chunk.process_id
    with pytest.raises(ShellError, match="does not support stdin"):
        await b.write_stdin(chunk.process_id, "x\n")
    chunk = await b.write_stdin(chunk.process_id, "", yield_time_ms=3000)
    assert chunk.exit_code == 1 and "late" in chunk.output
    httpx_mock.add_response(method="DELETE", url=f"{API}/sandbox/sb1", match_headers=auth)
    await b.close_session("s1")
    assert "s1" not in b._sandboxes
