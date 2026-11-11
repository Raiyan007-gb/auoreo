"""M7 hosts-b (plan 2.3): `environment`/`host` in the policy request, the `environment.exec`
linkage keys, the `env=<name>` usage tag and the example read_only_host policy."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from auoreo.core import hosts
from auoreo.core.db import UsageRow
from auoreo.core.permissions import PermissionEngine, Rule
from auoreo.core.policy import CompiledPolicy, PolicyEngine, compile_source, policy_engine
from auoreo.core.services import Services
from auoreo.core.usage import UsageLedger
from auoreo.gateway.budgets import BudgetTracker
from auoreo.gateway.fake import FakeGateway
from auoreo.gateway.types import Usage
from auoreo.runtime.request_layout import env_tag
from auoreo.tools.context import ToolContext
from auoreo.tools.environments import ProcRecord, pool_for
from auoreo.tools.stages import policy_context
from tests.unit import test_env_ssh
from tests.unit.test_env_ssh import (
    BASE,
    WHY,
    SshServer,
    audit_rows,
    host_file,
    session,
    tool,
)

ssh_server = test_env_ssh.ssh_server  # the in-process SSH server fixture
needs_server = test_env_ssh.pytestmark  # its commands run with bash

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = (ROOT / "policies" / "read_only_host.star").read_text()
FIELDS = {"host": "10.0.0.5", "user": "ops", "key_secret": "secret:SSH_KEY_PROD"}


def flags(services: Services, **on: bool) -> None:
    services.settings.m7.ssh.hosts = True
    for k, v in {"policy_fields": True, **on}.items():
        setattr(services.settings.m7.ssh, k, v)


def prod(services: Services, **over: Any) -> None:
    hosts.write_host(services.settings.home, "prod", {**FIELDS, "tags": ["readonly"], **over}, force=True)
    services.harness.reload()


def ctx_for(services: Services, workdir: Path) -> ToolContext:
    return ToolContext(
        session_id="s1", cwd=workdir, home=services.settings.home, services={"services": services}
    )


def req_of(
    services: Services, ctx: ToolContext, name: str, args: dict[str, Any] | None = None
) -> dict[str, Any]:
    return policy_context(SimpleNamespace(name=name), ctx, args)  # type: ignore[arg-type]


# ------------------------------------------------------------------ values per tool
async def test_fields_per_tool_and_none_for_local_tools(services: Services, workdir: Path) -> None:
    flags(services)
    prod(services)
    ctx = ctx_for(services, workdir)
    ctx.state.environment = "prod"
    want_host = {
        "name": "prod", "address": "10.0.0.5", "port": 22, "user": "ops",
        "origin": "host-file", "tags": ["readonly"], "jump": None,
    }  # fmt: skip
    for name in ("exec_command", "env_transfer"):
        got = req_of(services, ctx, name, {"cmd": "ls"})
        assert got["environment"] == "prod" and got["host"] == want_host
    assert "SSH_KEY_PROD" not in json.dumps(want_host)  # never a secret name
    for name in ("bash", "edit", "read", "write"):
        if name in services.harness.tools:
            got = req_of(services, ctx, name, {"command": "x", "path": "x"})
            assert "environment" not in got and "host" not in got  # -> None in the request
    got = req_of(services, ctx, "use_environment", {"name": "prod"})
    assert got["environment"] == "prod" and got["host"]["tags"] == ["readonly"]
    ctx.state.environment = None
    got = req_of(services, ctx, "exec_command", {"cmd": "ls"})
    assert got["environment"] is None and got["host"] is None
    assert req_of(services, ctx, "use_environment", {"name": "default"})["environment"] is None


async def test_write_stdin_uses_the_process_record_not_the_session(services: Services, workdir: Path) -> None:
    flags(services)
    prod(services)
    pool = pool_for(services)
    backend = pool.instance("prod")
    pool.record("s1", "p1", ProcRecord(backend, "prod", "cat"))
    ctx = ctx_for(services, workdir)
    ctx.state.environment = None  # the agent switched back after starting the process
    got = req_of(services, ctx, "write_stdin", {"session_id": "p1", "chars": "x"})
    assert got["environment"] == "prod" and got["host"]["tags"] == ["readonly"]
    unknown = req_of(services, ctx, "write_stdin", {"session_id": "nope", "chars": "x"})
    assert unknown["environment"] is None and unknown["host"] is None
    await pool.close_all()


async def test_flag_off_both_keys_are_none(services: Services, workdir: Path) -> None:
    services.settings.m7.ssh.hosts = True  # policy_fields stays off
    prod(services)
    ctx = ctx_for(services, workdir)
    ctx.state.environment = "prod"
    seen: list[dict[str, Any]] = []

    def capture(req: Any) -> object:
        seen.append(dict(req))
        return None

    engine = PermissionEngine([], policies=PolicyEngine([CompiledPolicy("cap", 1, "t", capture)]))
    engine.evaluate("bash", "ls", context=req_of(services, ctx, "exec_command", {"cmd": "ls"}))
    assert seen and seen[0]["environment"] is None and seen[0]["host"] is None


# ------------------------------------------------------------------ the example policy
def example() -> Any:
    return compile_source("read_only_host", EXAMPLE)


RO = {"name": "prod", "tags": ["readonly"]}


def ask(fn: Any, tool_name: str, permission: str, pattern: str, host: dict[str, Any] | None = RO) -> object:
    return fn({"tool": tool_name, "permission": permission, "pattern": pattern, "host": host})


@pytest.mark.parametrize(
    ("cmd", "verdict"),
    [
        ("systemctl restart api", "deny"),
        ("journalctl -u api -n 50", None),
        ("systemctl status api | grep active", None),
        ("cat /etc/hosts && ls -la /var/log", None),
        ("ls; rm -rf /tmp/x", "deny"),
        ("cat x > /etc/passwd", "deny"),
        ("echo $(reboot)", "deny"),
        ("echo `reboot`", "deny"),
        ("ls & reboot", "deny"),
        ("ls\nreboot", "deny"),
        ("git log || git push", "deny"),
        ("env rm -rf /", "deny"),
    ],
)
def test_example_policy_allowlist(cmd: str, verdict: object) -> None:
    assert ask(example(), "exec_command", "bash", cmd) == verdict


def test_example_policy_scope() -> None:
    fn = example()
    assert ask(fn, "write_stdin", "write_stdin", "p1") == "deny"
    assert ask(fn, "env_transfer", "sftp", "prod:upload:/etc/x") == "deny"
    assert ask(fn, "env_transfer", "sftp", "prod:download:/var/log/x") is None
    assert ask(fn, "exec_command", "bash", "reboot", host=None) is None  # local / not ssh
    assert ask(fn, "exec_command", "bash", "reboot", host={"name": "lab", "tags": []}) is None
    assert ask(fn, "bash", "bash", "reboot", host=None) is None
    header = EXAMPLE.split("\n\n", 1)[0]
    assert "enabled: false" in header and "not enforcement" in header


@needs_server
async def test_example_policy_through_the_executor(
    services: Services,
    ssh_server: SshServer,
    workdir: Path,
) -> None:
    """CUJ 1: the policy, copied to the runtime root and enabled, denies a restart on a
    `readonly` host and leaves a log read and every local tool alone."""
    flags(services)
    host_file(services, ssh_server, "prod", tags=["readonly"])
    (services.settings.home / "policies").mkdir(exist_ok=True)
    (services.settings.home / "policies" / "read_only_host.star").write_text(
        EXAMPLE.replace("# enabled: false", "# enabled: true")
    )
    services.harness.reload()
    rules = [*BASE, Rule(permission="ssh", pattern="prod", action="allow")]
    engine = PermissionEngine(rules, policies=policy_engine(services.harness))
    ctx = session(services, workdir, engine)
    text, err, _ = await tool(
        services, ctx, engine, "use_environment", {"name": "prod", "justification": WHY}
    )
    assert not err, text
    text, err, _ = await tool(services, ctx, engine, "exec_command", {"cmd": "systemctl restart api"})
    assert err and "policy:read_only_host" in text
    text, err, meta = await tool(
        services, ctx, engine, "exec_command", {"cmd": "echo journal-ok", "yield_time_ms": 3000}
    )
    assert not err and "journal-ok" in text, text
    text, err, meta = await tool(services, ctx, engine, "exec_command", {"cmd": "cat", "yield_time_ms": 200})
    assert meta.get("running"), text
    text, err, _ = await tool(
        services, ctx, engine, "write_stdin", {"session_id": meta["session_id"], "chars": "x\n"}
    )
    assert err and "policy:read_only_host" in text
    local = engine.evaluate("bash", "systemctl restart api", context=req_of(services, ctx, "bash", {}))
    assert local.action == "allow"  # the local shell is not the host
    await pool_for(services).close()


@needs_server
async def test_a_policy_can_deny_the_ssh_permission_by_tag(
    services: Services,
    ssh_server: SshServer,
    workdir: Path,
) -> None:
    flags(services)
    host_file(services, ssh_server, "prod", tags=["frozen"])
    body = (
        "def decide(req):\n"
        '    if req["permission"] == "ssh" and req["host"] != None and "frozen" in req["host"]["tags"]:\n'
        '        return "deny"\n'
        "    return None\n"
    )
    pe = PolicyEngine([CompiledPolicy("frozen", 100, "t", compile_source("frozen", body))])
    engine = PermissionEngine([*BASE, Rule(permission="ssh", pattern="*", action="allow")], policies=pe)
    ctx = session(services, workdir, engine)
    text, err, _ = await tool(
        services, ctx, engine, "use_environment", {"name": "prod", "justification": WHY}
    )
    assert err and "policy:frozen" in text and not ctx.state.ssh_granted
    assert not ssh_server.locals and not await audit_rows(services, "net.ssh")


# ------------------------------------------------------------------ environment.exec linkage
@needs_server
async def test_exec_rows_carry_linkage_and_cmd_only_when_redacted(
    services: Services,
    ssh_server: SshServer,
    workdir: Path,
) -> None:
    flags(services, exec_audit=True)
    host_file(services, ssh_server, "lab")
    engine = PermissionEngine([*BASE, Rule(permission="ssh", pattern="lab", action="allow")])
    ctx = session(services, workdir, engine)
    ctx.services["run_id"] = "run-1"
    await tool(services, ctx, engine, "use_environment", {"name": "lab"})
    _, err, _ = await tool(
        services, ctx, engine, "exec_command", {"cmd": "echo TOKEN=sk-abcdefabcdef0123456789"}
    )
    assert not err
    rows = await audit_rows(services, "environment.exec")
    grant = (await audit_rows(services, "net.ssh_grant"))[0].detail["grant_id"]
    d = rows[-1].detail
    assert {"tool": "exec_command", "call_id": "c", "run_id": "run-1", "grant_id": grant}.items() <= d.items()
    assert d["host_id"] == next(iter(ctx.state.ssh_granted)) and "cmd" not in d  # hash mode
    # opt in per host: the redacted text, never for stdin
    host_file(services, ssh_server, "lab", audit_commands="redacted")
    await tool(services, ctx, engine, "use_environment", {"name": "lab"})
    _, err, meta = await tool(
        services,
        ctx,
        engine,
        "exec_command",
        {"cmd": "echo TOKEN=sk-abcdefabcdef0123456789; cat", "yield_time_ms": 300},
    )
    d = (await audit_rows(services, "environment.exec"))[-1].detail
    assert "cmd" in d and "echo" in d["cmd"] and "sk-abcdefabcdef0123456789" not in json.dumps(d)
    await tool(
        services, ctx, engine, "write_stdin", {"session_id": meta["session_id"], "chars": "pw-hunter2\n"}
    )
    d = (await audit_rows(services, "environment.exec"))[-1].detail
    assert d["stdin"] is True and d["tool"] == "write_stdin" and "cmd" not in d
    assert "hunter2" not in json.dumps([r.detail for r in await audit_rows(services, "environment.exec")])
    await pool_for(services).close()


@needs_server
async def test_exec_rows_unchanged_with_the_flag_off(
    services: Services,
    ssh_server: SshServer,
    workdir: Path,
) -> None:
    flags(services, policy_fields=False)
    host_file(services, ssh_server, "lab", audit_commands="redacted")
    engine = PermissionEngine([*BASE, Rule(permission="ssh", pattern="lab", action="allow")])
    ctx = session(services, workdir, engine)
    await tool(services, ctx, engine, "use_environment", {"name": "lab"})
    await tool(services, ctx, engine, "exec_command", {"cmd": "echo hi"})
    d = (await audit_rows(services, "environment.exec"))[-1].detail
    assert set(d) == {"backend", "environment", "remote", "cmd_hash"}
    await pool_for(services).close()


# ------------------------------------------------------------------ usage tag
def test_env_tag_producer() -> None:
    st = SimpleNamespace(m7=SimpleNamespace(ssh=SimpleNamespace(usage_tag=True)))
    rt = SimpleNamespace(state=SimpleNamespace(environment="lab"))
    assert env_tag(st, rt) == {"tags": ["env=lab"]}
    rt.state.environment = "Weird,Name"
    assert env_tag(st, rt) == {"tags": ["env=other"]}
    rt.state.environment = None
    assert env_tag(st, rt) == {}
    st.m7.ssh.usage_tag = False
    rt.state.environment = "lab"
    assert env_tag(st, rt) == {}


async def test_env_tag_reaches_the_request_and_the_ledger(services: Services, gateway: FakeGateway) -> None:
    services.settings.m7.ssh.usage_tag = True
    sess = await services.create_session()
    rt = await services.coordinator.runtime(sess.id)
    rt.state.environment = "lab"
    await services.coordinator.submit(sess.id, "hello", interactive=False)
    await services.coordinator.wait(sess.id, 15)
    main = [r for r in gateway.requests if "purpose" not in r.metadata]
    assert main and main[0].metadata["tags"] == ["env=lab"]
    usage = Usage(
        prompt_tokens=1, completion_tokens=1, cost_usd=0.5, deployment="openai/x", tags=("env=lab",)
    )
    await UsageLedger(services.db).record(sess.id, "r", "default", usage)
    async with services.db.session() as s:
        stored = [r.tags for r in (await s.execute(select(UsageRow))).scalars()]
    assert "env=lab" in stored


def test_budgets_ignore_env_tags() -> None:
    cfg = SimpleNamespace(per_key_daily={}, provider_daily={}, tag_daily={"env=lab": 0.1, "nightly": 1.0})
    b = BudgetTracker(cfg)
    b.add("openai/x", 5.0, ["env=lab", "nightly"])
    assert b.spent("tag", "env=lab") == 0 and b.spent("tag", "nightly") == 5.0
    assert b.exhausted("openai/x", ["env=lab"]) is None  # a ceiling on env= never applies
    assert b.exhausted("openai/x", ["nightly"]) is not None
    b.seed([("openai/x", "env=lab,nightly", 2.0)])
    assert b.spent("tag", "env=lab") == 0 and b.spent("tag", "nightly") == 2.0


def test_example_policy_is_bundled_disabled(services: Services) -> None:
    loaded = services.harness.policies["read_only_host"]
    assert loaded.manifest.enabled is False
