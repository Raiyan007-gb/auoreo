"""G4b: monitor (stream / poll / follow), ops_gpu (NVML, honest no-GPU) and ops_ping
(ICMP via ping3, TCP-connect fallback)."""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from auoreo.core.permissions import PermissionEngine, Rule
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.tools.context import ToolContext

ALLOW_BASH = [Rule(permission="bash", pattern="*", action="allow")]


async def call(
    services: Services, workdir: Path, name: str, args: dict, *, events: list | None = None
) -> tuple[str, bool, dict[str, Any]]:
    async def emit(event_type: str, data: dict[str, Any]) -> None:
        if events is not None:
            events.append((event_type, data))

    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home, emit=emit)
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine(ALLOW_BASH),
    )
    return out.result.content, out.result.is_error, out.result.meta


def test_manifests(services: Services) -> None:
    tools = services.harness.tools
    assert tools["monitor"].manifest.interrupt == "cancel"
    gpu = tools["ops_gpu"].manifest
    assert (gpu.toolset, gpu.permission_name, gpu.default_action, gpu.net) == ("ops", "ops", "allow", "none")
    ping = tools["ops_ping"].manifest
    assert (ping.permission_name, ping.pattern_arg, ping.net) == ("netprobe", "host", "direct")


# ------------------------------------------------------------------ monitor
async def test_monitor_stream_matches_and_emits_progress(services: Services, workdir: Path) -> None:
    events: list = []
    cmd = "for i in 1 2 3; do echo line$i; sleep 0.05; done; echo READY port=8080; sleep 30"
    text, err, meta = await call(
        services, workdir, "monitor", {"cmd": cmd, "until": r"READY port=\d+", "timeout_s": 20}, events=events
    )
    assert not err and meta["status"] == "matched" and meta["match"] == "READY port=8080", text
    assert "line2" in text
    progress = [d for t, d in events if t == "tool.progress"]
    assert progress and progress[-1]["tool"] == "monitor" and "READY port=8080" in progress[-1]["lines"]


async def test_monitor_stream_exit_without_match(services: Services, workdir: Path) -> None:
    text, err, meta = await call(services, workdir, "monitor", {"cmd": "echo a; exit 3", "until": "zzz"})
    assert err and meta["status"] == "exited" and meta["exit_code"] == 3 and "without a match" in text
    text, err, meta = await call(services, workdir, "monitor", {"cmd": "echo a"})
    assert not err and meta["status"] == "exited"  # no condition: the stream ending is success


async def test_monitor_poll_mode(services: Services, workdir: Path) -> None:
    async def later() -> None:
        await asyncio.sleep(0.6)
        (workdir / "flag").write_text("x")

    task = asyncio.create_task(later())
    args = {"cmd": "test -f flag && echo state=up || echo state=down", "every_s": 1, "until": "state=up"}
    text, err, meta = await call(services, workdir, "monitor", {**args, "timeout_s": 15})
    await task
    assert not err and meta["status"] == "matched" and meta["match"] == "state=up", text
    assert meta["lines"] >= 2 and "state=down" in text


async def test_monitor_follow_file(services: Services, workdir: Path) -> None:
    log = workdir / "app.log"
    log.write_text("old ERROR line\n")

    async def writer() -> None:
        await asyncio.sleep(0.3)
        with log.open("a") as fh:
            fh.write("info ok\nERROR boom\n")

    task = asyncio.create_task(writer())
    text, err, meta = await call(
        services, workdir, "monitor", {"path": "app.log", "until": "ERROR", "timeout_s": 10}
    )
    await task
    assert not err and meta["match"] == "ERROR boom"  # the pre-existing line is skipped
    text, err, meta = await call(
        services,
        workdir,
        "monitor",
        {"path": "app.log", "until": "error", "ignore_case": True, "from_start": True},
    )
    assert not err and meta["match"] == "old ERROR line"


async def test_monitor_timeout_and_refusals(services: Services, workdir: Path) -> None:
    (workdir / "quiet.log").write_text("")
    text, err, meta = await call(
        services, workdir, "monitor", {"path": "quiet.log", "until": "x", "timeout_s": 1}
    )
    assert err and meta["status"] == "timeout" and "timed out after 1s" in text
    (workdir / ".env").write_text("SECRET=1\n")
    text, err, _ = await call(services, workdir, "monitor", {"path": ".env", "until": "x"})
    assert err and "secret" in text
    text, err, _ = await call(services, workdir, "monitor", {"cmd": "true", "path": "quiet.log"})
    assert err and "exactly one of cmd or path" in text
    text, err, _ = await call(services, workdir, "monitor", {"cmd": "true", "until": "("})
    assert err and "invalid until regex" in text


async def test_monitor_timeout_capped_by_settings(services: Services, workdir: Path) -> None:
    services.settings.tools.monitor_max_s = 1
    (workdir / "q.log").write_text("")
    text, err, meta = await call(
        services, workdir, "monitor", {"path": "q.log", "until": "x", "timeout_s": 300}
    )
    assert meta["status"] == "timeout" and "after 1s" in text


# ------------------------------------------------------------------ ops_gpu
async def test_ops_gpu_honest_without_nvidia(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = types.ModuleType("pynvml")

    def no_lib() -> None:
        raise RuntimeError("NVML Shared Library Not Found")

    fake.nvmlInit = no_lib  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pynvml", fake)
    text, err, meta = await call(services, workdir, "ops_gpu", {})
    assert not err and "no NVIDIA GPU or driver" in text and meta["available"] is False

    monkeypatch.setitem(sys.modules, "pynvml", None)  # not installed
    text, err, meta = await call(services, workdir, "ops_gpu", {})
    assert not err and "pynvml is not installed" in text and meta["reason"] == "pynvml-missing"


async def test_ops_gpu_reads_devices(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ns = types.SimpleNamespace
    fake = types.ModuleType("pynvml")
    calls: list[str] = []

    def unsupported(h: object) -> None:
        raise RuntimeError("Not Supported")

    fake.__dict__.update(
        NVML_TEMPERATURE_GPU=0,
        nvmlInit=lambda: calls.append("init"),
        nvmlShutdown=lambda: calls.append("shutdown"),
        nvmlDeviceGetCount=lambda: 1,
        nvmlSystemGetDriverVersion=lambda: "580.1",
        nvmlDeviceGetHandleByIndex=lambda i: f"h{i}",
        nvmlDeviceGetName=lambda h: b"NVIDIA L4",
        nvmlDeviceGetUtilizationRates=lambda h: ns(gpu=37, memory=12),
        nvmlDeviceGetMemoryInfo=lambda h: ns(used=2048 * 1024 * 1024, total=24576 * 1024 * 1024),
        nvmlDeviceGetTemperature=lambda h, kind: 51,
        nvmlDeviceGetPowerUsage=lambda h: 42_000,
        nvmlDeviceGetEnforcedPowerLimit=unsupported,
        nvmlDeviceGetComputeRunningProcesses=lambda h: [ns(pid=4242, usedGpuMemory=512 * 1024 * 1024)],
    )
    monkeypatch.setitem(sys.modules, "pynvml", fake)
    text, err, meta = await call(services, workdir, "ops_gpu", {"processes": True})
    assert not err and meta == {"available": True, "gpus": 1}
    assert "driver 580.1" in text and "NVIDIA L4" in text and "util 37%" in text
    assert "memory 2048/24576 MiB (8%)" in text and "51°C" in text and "power 42 W" in text
    assert "pid 4242: 512 MiB" in text and calls == ["init", "shutdown"]


# ------------------------------------------------------------------ ops_ping
async def test_ops_ping_tcp_open_and_refused(services: Services, workdir: Path) -> None:
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        text, err, meta = await call(
            services, workdir, "ops_ping", {"host": "127.0.0.1", "port": port, "count": 2}
        )
        assert not err and meta["method"] == "tcp" and meta["reachable"] and meta["received"] == 2
        assert f"port {port} open" in text and "rtt min/avg/max" in text
    finally:
        server.close()
        await server.wait_closed()
    text, err, meta = await call(
        services, workdir, "ops_ping", {"host": "127.0.0.1", "port": port, "count": 1}
    )
    assert not err and meta["reachable"] and f"port {port} refused" in text  # RST = host is up


async def test_ops_ping_falls_back_to_tcp_without_ping3(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setitem(sys.modules, "ping3", None)
    services.settings.tools.ping_tcp_ports = [port]
    try:
        text, err, meta = await call(services, workdir, "ops_ping", {"host": "127.0.0.1", "count": 1})
    finally:
        server.close()
        await server.wait_closed()
    assert not err and meta["method"] == "tcp" and "ICMP unavailable: ping3 is not installed" in text
    text, err, _ = await call(services, workdir, "ops_ping", {"host": "127.0.0.1", "method": "icmp"})
    assert err and "retry with method tcp" in text


async def test_ops_ping_icmp_with_fake_ping3(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = types.ModuleType("ping3")
    fake.ping = lambda ip, timeout, unit: 0.0021  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ping3", fake)
    text, err, meta = await call(services, workdir, "ops_ping", {"host": "127.0.0.1", "count": 2})
    assert not err and meta["method"] == "icmp" and meta["received"] == 2 and "via ICMP" in text
    assert meta["avg_ms"] == pytest.approx(2.1)


async def test_ops_ping_unresolvable(services: Services, workdir: Path) -> None:
    text, err, _ = await call(services, workdir, "ops_ping", {"host": "no-such-host.invalid"})
    assert err and "cannot resolve host" in text
