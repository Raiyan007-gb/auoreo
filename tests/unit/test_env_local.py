"""local environment backend (wraps the persistent-shell registry)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from auoreo.backends import BackendStatus
from auoreo.backends.environment import EnvironmentBackend, ExecRequest
from auoreo.core.services import Services
from auoreo.tools.shell_sessions import ShellError, registry_for


def local_cls(services: Services) -> Any:
    bdef = services.harness.select_backend("environment", None)
    assert bdef is not None and bdef.manifest.name == "local" and bdef.manifest.default
    return bdef.load()


async def test_default_is_local_and_checks(services: Services) -> None:
    b = services.backend("environment")
    assert isinstance(b, EnvironmentBackend) and b.name == "local" and b.remote is False
    status = await b.check()
    assert status.ok and "unsandboxed" in status.detail


async def test_unbound_exec_stdin_and_close(services: Services, workdir: Path) -> None:
    b = local_cls(services)(services.settings)  # unbound: its own registry
    chunk = await b.exec_command(ExecRequest("echo hi; exit 2", workdir=str(workdir)), session_id="s1")
    assert chunk.exit_code == 2 and "hi" in chunk.output and chunk.pid
    chunk = await b.exec_command(ExecRequest("read x; echo got:$x", yield_time_ms=100), session_id="s1")
    assert chunk.exit_code is None and chunk.process_id
    pid = chunk.process_id
    chunk = await b.write_stdin(pid, "zz\n", yield_time_ms=5000)
    assert chunk.exit_code == 0 and "got:zz" in chunk.output
    with pytest.raises(ShellError, match="no running"):
        await b.write_stdin(pid, "", yield_time_ms=10)
    chunk = await b.exec_command(ExecRequest("sleep 30", yield_time_ms=50), session_id="s2")
    await b.close_session("s2")
    assert b.registry.find(chunk.process_id) is None
    await b.close()


async def test_bound_shares_the_server_registry(services: Services, workdir: Path) -> None:
    b = local_cls(services)(services.settings)
    b.bind(services)
    assert b.registry is registry_for(services)
    chunk = await b.exec_command(ExecRequest("sleep 30", yield_time_ms=50), session_id="s1")
    assert registry_for(services).get("s1", chunk.process_id)
    await b.close()  # never closes the shared registry …
    assert registry_for(services).find(chunk.process_id) is not None
    await b.close_session("s1")  # … but closes the session's processes
    assert registry_for(services).find(chunk.process_id) is None


async def test_process_ids_unique_across_sessions(services: Services, workdir: Path) -> None:
    b = local_cls(services)(services.settings)
    ids = set()
    for i in range(6):
        chunk = await b.exec_command(ExecRequest("sleep 30", yield_time_ms=10), session_id=f"s{i}")
        ids.add(chunk.process_id)
    assert len(ids) == 6
    await b.close()


class _Enforcing:
    name = "bwrap"

    async def check(self) -> BackendStatus:
        return BackendStatus(True)

    def capabilities(self) -> dict[str, bool]:
        return {"fs_isolation": True, "net_isolation": True}


async def test_refuses_under_enforcing_sandbox(services: Services) -> None:
    services._backends["sandbox"] = _Enforcing()
    b = local_cls(services)(services.settings)
    b.bind(services)
    with pytest.raises(ShellError, match="unsandboxed"):
        await b.exec_command(ExecRequest("echo hi"), session_id="s1")
    with pytest.raises(ShellError, match="unsandboxed"):
        await b.write_stdin("abc", "x")
