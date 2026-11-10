"""`use_environment` + per-session routing of exec_command / write_stdin (W2-A)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from auoreo.core import hosts
from auoreo.core.diagnostics import run_checks
from auoreo.core.permissions import PermissionEngine, Rule
from auoreo.core.services import Services
from auoreo.core.settings import NamedEnvironment
from auoreo.gateway.types import ToolCall
from auoreo.tools.context import ToolContext
from auoreo.tools.environments import pool_for, settings_for

ALLOW = [
    Rule(permission="bash", pattern="*", action="allow"),
    Rule(permission="environment", pattern="*", action="allow"),
]


async def call(
    services: Services, ctx: ToolContext, name: str, args: dict[str, Any], rules: list[Rule] = ALLOW
) -> tuple[str, bool, dict[str, Any]]:
    out = await services.coordinator.runner.executor.run(
        ToolCall("c", name, json.dumps(args)),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine(rules),
    )
    return out.result.content, out.result.is_error, out.result.meta


def make_ctx(services: Services, workdir: Path) -> ToolContext:
    return ToolContext(session_id="s1", cwd=workdir, home=services.settings.home)


def test_manifest(services: Services) -> None:
    m = services.harness.tools["use_environment"].manifest
    assert (m.permission_name, m.pattern_arg, m.default_action, m.exposure) == (
        "environment",
        "name",
        "ask",
        "deferred",
    )


async def test_needs_permission(services: Services, workdir: Path) -> None:
    services.settings.environments = {"box": NamedEnvironment(backend="local")}
    ctx = make_ctx(services, workdir)
    text, err, _ = await call(services, ctx, "use_environment", {"name": "box"}, rules=[])
    assert err and "needs approval" in text and ctx.state.environment is None


async def test_list_switch_reset_and_unknown(services: Services, workdir: Path) -> None:
    services.settings.environments = {"box": NamedEnvironment(backend="local", description="scratch box")}
    ctx = make_ctx(services, workdir)
    text, err, _ = await call(services, ctx, "use_environment", {})
    assert not err and "current: default" in text and "box: backend local — scratch box" in text
    text, err, _ = await call(services, ctx, "use_environment", {"name": "nope"})
    assert err and "unknown environment 'nope'" in text
    text, err, meta = await call(services, ctx, "use_environment", {"name": "box"})
    assert not err and ctx.state.environment == "box" and meta["backend"] == "local"
    text, err, _ = await call(services, ctx, "exec_command", {"cmd": "echo routed"})
    assert not err and "routed" in text
    assert "box" in pool_for(services).instances  # a separate instance for the named env
    text, err, _ = await call(services, ctx, "use_environment", {"name": "default"})
    assert not err and ctx.state.environment is None


async def test_switch_refused_when_backend_not_ready(services: Services, workdir: Path) -> None:
    services.settings.environments = {"cloud": NamedEnvironment(backend="e2b")}
    ctx = make_ctx(services, workdir)
    text, err, _ = await call(services, ctx, "use_environment", {"name": "cloud"})
    assert err and "not ready" in text and "E2B_API_KEY" in text and ctx.state.environment is None


async def test_running_process_stays_with_its_environment(services: Services, workdir: Path) -> None:
    services.settings.environments = {"box": NamedEnvironment(backend="local")}
    ctx = make_ctx(services, workdir)
    ctx.state.environment = "box"
    text, err, meta = await call(services, ctx, "exec_command", {"cmd": "cat", "yield_time_ms": 100})
    assert not err and meta.get("running")
    ctx.state.environment = None  # switched back; the process still lives in "box"
    text, err, _ = await call(
        services, ctx, "write_stdin", {"session_id": meta["session_id"], "chars": "hey\n"}
    )
    assert not err and "hey" in text
    other = ToolContext(session_id="s2", cwd=workdir, home=services.settings.home)
    text, err, _ = await call(services, other, "write_stdin", {"session_id": meta["session_id"]})
    assert err and "no running exec_command session" in text  # never across sessions
    await pool_for(services).close_session("s1")


async def test_overrides_are_validated_and_applied(services: Services) -> None:
    entry = NamedEnvironment(backend="docker", environment_docker_network="bridge")
    st = settings_for(services.settings, entry)
    assert (
        st.environment_docker_network == "bridge" and services.settings.environment_docker_network == "none"
    )
    try:
        settings_for(services.settings, NamedEnvironment(backend="docker", no_such_setting=1))
    except ValueError as exc:
        assert "no_such_setting" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unknown override accepted")
    services.settings.environments = {"bad": NamedEnvironment(backend="nope")}
    ctx = ToolContext(session_id="s1", cwd=Path("."), home=services.settings.home)
    text, err, _ = await call(services, ctx, "use_environment", {"name": "bad"})
    assert err and "nope" in text


async def test_doctor_lists_environment_rows(services: Services) -> None:
    services.settings.environments = {
        "box": NamedEnvironment(backend="local"),
        "cloud": NamedEnvironment(backend="daytona"),
    }
    checks = await run_checks(services.settings, services=services)
    by = {c.check: c for c in checks}
    assert by["backend:environment"].level == "ok" and "local" in by["backend:environment"].detail
    assert by["environment:box"].level == "ok" and "local (local)" in by["environment:box"].detail
    cloud = by["environment:cloud"]
    assert cloud.level == "warn" and re.search(r"DAYTONA_API_KEY", cloud.detail)


async def test_unknown_name_lists_both_sources_when_hosts_are_on(services: Services, workdir: Path) -> None:
    services.settings.environments = {"box": NamedEnvironment(backend="local")}
    fields = {"host": "lab.example", "user": "u", "key_secret": "secret:SSH_KEY_LAB"}
    hosts.write_host(services.settings.home, "lab", fields)
    services.harness.reload()
    ctx = make_ctx(services, workdir)
    text, err, _ = await call(services, ctx, "use_environment", {"name": "nope"})
    assert err and text.endswith(
        "unknown environment 'nope' (settings.environments: box)"
    )  # flag off: as before
    text, err, _ = await call(services, ctx, "use_environment", {})
    assert not err and "lab" not in text
    services.settings.m7.ssh.hosts = True
    text, err, _ = await call(services, ctx, "use_environment", {"name": "nope"})
    assert err and "unknown environment 'nope' (settings.environments: box; hosts: lab)" in text
    text, err, _ = await call(services, ctx, "use_environment", {})
    assert not err and "- box: backend local" in text and "- lab: backend ssh — ssh host file" in text
    assert "lab.example" not in text and "SSH_KEY_LAB" not in text
    manifest = services.harness.tools["use_environment"].manifest
    assert "justification" in manifest.parameters["properties"]
    assert manifest.net == "none"  # documented deviation: the `ssh` gate, not the net stage, covers it
