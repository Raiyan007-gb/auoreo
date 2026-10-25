"""Plugin host: discovery → install → start → tools/deliveries/connectors → crash/restart → sync."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import types
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.plugins import install as inst
from auoreo.plugins.host import PluginHost
from auoreo.tools.context import ToolContext

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "plugins" / "echo"
PY = f"{sys.version_info.major}.{sys.version_info.minor}"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


async def until(pred: Callable[[], bool], timeout: float = 15.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.05)


def plugin_settings(home: Path, **extra: Any) -> Settings:
    base: dict[str, Any] = {
        "home": home,
        "gateway": "fake",
        "approval_timeout_s": 5,
        "plugins_python": PY,
        "plugins_backoff_scale": 0.0,
    }
    return Settings(**{**base, **extra})


@pytest.fixture
def echo_home(home: Path) -> Path:
    shutil.copytree(FIXTURE, home / "plugins" / "echo")
    return home


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    return tmp_path / "deliveries.log"


async def make_services(home: Path, workdir: Path, **extra: Any) -> Services:
    return await Services.create(
        plugin_settings(home, **extra), cwd=workdir, gateway=FakeGateway(), with_mcp=False, with_memory=False
    )


@pytest.fixture
async def svc(echo_home: Path, workdir: Path, log_path: Path) -> AsyncIterator[Services]:
    s = await make_services(echo_home, workdir, plugins={"echo": {"log_path": str(log_path)}})
    try:
        yield s
    finally:
        await s.close()


def host_of(s: Services) -> PluginHost:
    host = s.ext["plugins"]
    assert isinstance(host, PluginHost)
    return host


async def call(s: Services, workdir: Path, tool: str, args: dict[str, Any], sid: str = "ses_test") -> Any:
    ctx = ToolContext(session_id=sid, cwd=workdir, home=s.settings.home)
    engine = PermissionEngine([Rule(permission="plugin__echo__*", pattern="*", action="allow")])
    out = await s.coordinator.runner.executor.run(
        ToolCall("c1", tool, json.dumps(args)), tools=s.harness.tools, ctx=ctx, engine=engine
    )
    return out.result


async def test_install_start_register_and_round_trip(svc: Services, workdir: Path, log_path: Path) -> None:
    host = host_of(svc)
    row = next(r for r in host.rows() if r["name"] == "echo")
    assert row["status"] == "running", row
    assert row["verify"] == "ok"
    lock = json.loads((svc.settings.home / "plugins" / "echo" / "lock.json").read_text())
    assert lock["name"] == "echo" and lock["python"] == PY and len(lock["sha256"]) == 64
    assert (svc.settings.home / "plugins" / "echo" / ".venv").is_dir()
    assert row["tools"] == [f"plugin__echo__{t}" for t in ("echo", "crash", "secret", "slow")]

    tool = svc.harness.tools["plugin__echo__echo"]
    m = tool.manifest
    assert (m.toolset, m.default_action, m.exposure, m.permission) == (
        "plugin:echo",
        "ask",
        "deferred",
        "plugin__echo__echo",
    )
    res = await call(svc, workdir, "plugin__echo__echo", {"text": "hi"})
    assert res.content == "echo: hi [echo]" and not res.is_error  # AUOREO_PLUGIN reached the child

    receipt = await host.deliveries["echo_log"].send("t1", "hello", meta={})
    assert receipt.ok and receipt.external_id == "line-t1"
    assert read(log_path) == "t1\thello\n"
    assert host.delivery_registry is False  # no auoreo.backends.delivery in this checkout
    assert [s.name for s in host.skills("echo")] == ["echo-usage"]
    # bundled telegram/discord are discovered but disabled by default
    statuses = {r["name"]: r["status"] for r in host.rows()}
    assert statuses["telegram"] == "disabled" and statuses["discord"] == "disabled"


async def test_lock_skips_reinstall_and_verify_detects_changes(svc: Services, workdir: Path) -> None:
    home = svc.settings.home
    src = home / "plugins" / "echo"
    first = await inst.install("echo", src, home, python=PY)
    assert first.fresh is False  # the host already installed it; lock matches
    (src / "extra.txt").write_text("x")
    assert inst.verify(home, "echo", src) == "mismatch"


async def test_secret_get_only_for_declared_names(svc: Services, workdir: Path) -> None:
    svc.secrets.set("echo/token", "s3cret")
    svc.secrets.set("other/token", "nope")
    ok = await call(svc, workdir, "plugin__echo__secret", {"name": "echo/token"})
    assert not ok.is_error and ok.content == "secret=***"  # resolved, then redacted by the executor
    denied = await call(svc, workdir, "plugin__echo__secret", {"name": "other/token"})
    assert denied.is_error and "not declared in consumes" in denied.content and "nope" not in denied.content


async def test_tool_timeout_answers_with_an_error(svc: Services, workdir: Path) -> None:
    res = await call(svc, workdir, "plugin__echo__slow", {})
    assert res.is_error and "did not answer" in res.content


async def test_crash_restarts_with_backoff_and_emits_events(svc: Services, workdir: Path) -> None:
    host = host_of(svc)
    sub = svc.bus.subscribe(frozenset({"server"}))
    res = await call(svc, workdir, "plugin__echo__crash", {})
    assert res.is_error
    seen: list[str] = []

    async def collect() -> None:
        while "plugin.restarted" not in seen:
            seen.append((await sub.queue.get()).type)

    await asyncio.wait_for(collect(), 15)
    assert "plugin.crashed" in seen
    assert host.plugins["echo"].status == "running" and len(host.plugins["echo"].restarts) == 1
    res = await call(svc, workdir, "plugin__echo__echo", {"text": "back"})
    assert res.content.startswith("echo: back")


async def test_dead_plugin_tools_answer_with_an_error(svc: Services, workdir: Path) -> None:
    host = host_of(svc)
    await host.stop_plugin("echo")
    assert host.plugins["echo"].status == "stopped"
    assert "plugin__echo__echo" not in svc.harness.tools
    await host.start_plugin("echo")
    peer = host.plugins["echo"].peer
    assert peer is not None and peer.proc is not None
    st = host.plugins["echo"]
    st.status = "stopped"  # suppress auto-restart for this test
    peer.proc.kill()
    await until(lambda: not peer.alive)
    res = await call(svc, workdir, "plugin__echo__echo", {"text": "x"})
    assert res.is_error and "not running" in res.content


async def test_inbound_connector_message_creates_a_session_and_replies(
    echo_home: Path, workdir: Path, log_path: Path
) -> None:
    s = await make_services(
        echo_home, workdir, plugins={"echo": {"log_path": str(log_path), "inbound": True}}
    )
    try:
        await until(lambda: log_path.exists() and read(log_path).strip() != "", 20)
        target, text = read(log_path).strip().split("\t", 1)
        assert (target, text) == ("chat-1", "echo: hello from echo")
        mapping = json.loads((echo_home / "plugins" / "echo" / "sessions.json").read_text())
        row = await s.get_session(mapping["chat-1"])
        assert row is not None and row.title.startswith("echo: alice")
    finally:
        await s.close()


async def test_invalid_config_disables_the_plugin_with_a_pointer(echo_home: Path, workdir: Path) -> None:
    s = await make_services(echo_home, workdir, plugins={"echo": {"greeting": 5}})
    try:
        st = host_of(s).plugins["echo"]
        assert st.status == "invalid_config"
        assert st.error is not None and "/greeting" in st.error
        assert st.peer is None and "plugin__echo__echo" not in s.harness.tools
    finally:
        await s.close()


async def test_plugins_enabled_false_starts_nothing(echo_home: Path, workdir: Path) -> None:
    s = await make_services(echo_home, workdir, plugins_enabled=False)
    try:
        assert host_of(s).plugins["echo"].status == "disabled"
        assert not any(n.startswith("plugin__") for n in s.harness.tools)
    finally:
        await s.close()


async def test_sync_after_reload_restarts_changed_and_stops_removed(svc: Services) -> None:
    host = host_of(svc)
    src = svc.settings.home / "plugins" / "echo"
    old_pid = host.plugins["echo"].peer.proc.pid  # type: ignore[union-attr]
    (src / "plugin.py").write_text((src / "plugin.py").read_text() + "\n# changed\n")
    svc.harness.reload()
    changes = await host.sync()
    assert changes["restarted"] == ["echo"]
    assert host.plugins["echo"].peer.proc.pid != old_pid  # type: ignore[union-attr]

    (src / "plugin.yaml").unlink()
    svc.harness.reload()
    changes = await host.sync()
    assert changes["stopped"] == ["echo"] and "echo" not in host.plugins
    assert not any(n.startswith("plugin__echo__") for n in svc.harness.tools)


async def test_watcher_syncs_after_a_harness_reload(svc: Services) -> None:
    host = host_of(svc)
    (svc.settings.home / "plugins" / "echo" / "plugin.yaml").unlink()
    svc.harness.reload()  # e.g. POST /api/harness/reload; no explicit host.sync()
    await until(lambda: "echo" not in host.plugins, 5)
    assert svc.bus.subscriber_count == 0  # the host watches without holding a bus subscription


async def test_deliveries_register_in_the_shared_registry_when_present(
    echo_home: Path, workdir: Path, log_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry: dict[str, Any] = {}
    fake = types.ModuleType("auoreo.backends.delivery")

    class DeliveryReceipt:
        def __init__(self, ok: bool, detail: str = "", external_id: str | None = None) -> None:
            self.ok, self.detail, self.external_id = ok, detail, external_id

    class DeliveryProvider:
        pass

    fake.DeliveryReceipt = DeliveryReceipt  # type: ignore[attr-defined]
    fake.DeliveryProvider = DeliveryProvider  # type: ignore[attr-defined]
    fake.register = registry.__setitem__  # type: ignore[attr-defined]
    fake.unregister = lambda name: registry.pop(name, None)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "auoreo.backends.delivery", fake)
    s = await make_services(echo_home, workdir, plugins={"echo": {"log_path": str(log_path)}})
    try:
        assert host_of(s).delivery_registry is True
        provider = registry["echo_log"]
        assert isinstance(provider, DeliveryProvider)
        receipt = await provider.send("t9", "via registry", meta={})
        assert isinstance(receipt, DeliveryReceipt) and receipt.ok
        assert read(log_path) == "t9\tvia registry\n"
    finally:
        await s.close()
    assert "echo_log" not in registry
