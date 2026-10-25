"""G1b: permission algebra to spec §19 — reasons/rule ids, visible tools, external
directories, sensitive writes, unattended policy, sharded rules + verdict cache + perf."""

from __future__ import annotations

import json
import random
import statistics
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from auoreo.core.approvals import ApprovalBroker, grouped, reply_with_grants, unattended_action
from auoreo.core.permissions import (
    REASONS,
    SENSITIVE_NOTE,
    PermissionEngine,
    expand,
    hidden_by_rules,
    is_sensitive_write,
    visible_tools,
)
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.tools.context import ToolContext

DEFAULTS = Path(__file__).resolve().parents[2] / "permissions" / "defaults.yaml"


def R(permission: str, pattern: str, action: str) -> Rule:
    return Rule(permission=permission, pattern=pattern, action=action)  # type: ignore[arg-type]


def default_rules() -> list[Rule]:
    import yaml

    return [Rule(**r) for r in yaml.safe_load(DEFAULTS.read_text())["rules"]]


# ------------------------------------------------------------------ reasons + ids
def test_decision_reason_enum_and_rule_id() -> None:
    e = PermissionEngine([R("bash", "*", "ask"), R("bash", "ls", "allow")])
    e.session_rules.append(R("bash", "make", "allow"))
    e.grant_always("bash", "pytest")
    d = e.evaluate("bash", "ls")
    assert (d.action, d.reason, d.rule_id) == ("allow", "rule", "rules[1]")
    assert e.evaluate("bash", "make").reason == "session"
    assert e.evaluate("bash", "make").rule_id == "session[0]"
    assert e.evaluate("bash", "pytest").reason == "approved"
    assert e.evaluate("bash", "rm -rf /").reason == "floor"
    assert e.evaluate("read", "/x").reason == "default"
    for d in (e.evaluate("bash", "ls"), e.evaluate("bash", "rm -rf /"), e.evaluate("read", "/x")):
        assert d.reason in REASONS


def test_visible_tools_hides_last_deny_star() -> None:
    rules = [
        R("edit", "*", "ask"),
        R("edit", "*", "deny"),
        R("read", "*", "deny"),
        R("read", "*.md", "allow"),
    ]
    assert hidden_by_rules("edit", rules) and not hidden_by_rules("read", rules)
    assert not hidden_by_rules("bash", rules)
    tools = [
        SimpleNamespace(name=n, manifest=SimpleNamespace(permission_name=p))
        for n, p in [("edit", "edit"), ("write", "edit"), ("read", "read"), ("bash", "bash")]
    ]
    assert [t.name for t in visible_tools(tools, rules)] == ["read", "bash"]
    assert [t.name for t in visible_tools(tools, [*rules, R("*", "*", "deny")])] == []


def test_build_spec_filters_denied_tools(services: Services) -> None:
    from auoreo.harness.orchestrator import build_spec

    agent = services.harness.agent("default")
    agent.manifest.permissions.append(R("edit", "*", "deny"))
    try:
        spec = build_spec(services.harness, "default", "fake/model", "manual")
    finally:
        agent.manifest.permissions.pop()
    names = {t.name for t in (*spec.tools, *spec.deferred)}
    assert "read" in names and not names & {"edit", "write", "str_replace", "apply_patch"}


# ------------------------------------------------------------ external directory
def test_external_directory_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUOREO_HOME", str(tmp_path / "ahome"))
    cwd = tmp_path / "repo"
    e = PermissionEngine(default_rules(), context={"cwd": str(cwd)})
    assert e.external_directory(cwd / "src" / "a.py", cwd) is None
    assert e.external_directory("/opt/other/file", cwd).action == "ask"  # type: ignore[union-attr]
    tmp = Path(tempfile.gettempdir()) / "scratch.txt"
    assert e.external_directory(tmp, "/srv/repo").action == "allow"  # type: ignore[union-attr]
    skills = tmp_path / "ahome" / "skills" / "x" / "SKILL.md"
    assert e.external_directory(skills, "/srv/repo").action == "allow"  # type: ignore[union-attr]
    assert expand("$CWD/.auoreo/plans/*", "/w") == "/w/.auoreo/plans/*"


async def test_executor_asks_external_directory(services: Services, workdir: Path) -> None:
    outside = Path("/opt") / "auoreo-g1b-outside.txt"
    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home, harness=services.harness)
    rules = [*default_rules(), R("read", "*", "allow")]
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", "read", json.dumps({"path": str(outside)})),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine(rules, context={"cwd": str(workdir)}),
    )
    assert out.result.is_error and f"{outside.parent}/*" in out.result.content
    assert "non-interactive" in out.result.content


# --------------------------------------------------------------- sensitive writes
def test_sensitive_write_asks_even_under_yolo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("auoreo.core.permissions.YOLO_FROZEN", True)
    ssh = str(tmp_path / ".ssh" / "config")
    for mode in ("yolo", "acceptEdits", "manual"):
        e = PermissionEngine([R("edit", "*", "allow")], mode=mode)  # type: ignore[arg-type]
        d = e.evaluate("edit", ssh)
        assert (d.action, d.reason, d.note) == ("ask", "floor", SENSITIVE_NOTE), mode
    e = PermissionEngine([R("edit", "*", "allow")], mode="yolo")
    assert e.evaluate("edit", str(tmp_path / "proj" / "main.py")).action == "allow"
    for p in (
        "/etc/hosts",
        str(tmp_path / ".bashrc"),
        "/w/.git/hooks/pre-commit",
        str(tmp_path / ".aws/credentials"),
    ):
        assert is_sensitive_write(p), p
    # floors still win: a secret file is denied, never merely asked
    assert e.evaluate("edit", str(tmp_path / ".ssh" / ".env")).action == "deny"
    assert e.evaluate("edit", ssh).detail.startswith("floor: sensitive")


# --------------------------------------------------------------- approvals scope
async def test_approval_always_must_be_subset_of_patterns() -> None:
    events: list[tuple[str, dict]] = []

    async def emit(sid: str, kind: str, data: dict) -> None:
        events.append((kind, data))

    broker = ApprovalBroker(emit, timeout_s=0.5)
    bad = await broker.request("s", "bash", "bash", "ls", "d", patterns=["ls"], always=["ls*"])
    assert not bad.granted and "subset" in (bad.message or "") and not events
    engine = PermissionEngine([])
    import asyncio

    task = asyncio.create_task(
        broker.request(
            "s", "bash", "bash", "git status", "d", patterns=["git status", "git *"], always=["git *"]
        )
    )
    await asyncio.sleep(0)
    rid = broker.pending()[0].request_id
    assert await reply_with_grants(broker, lambda _sid: engine, rid, "always")
    assert (await task).granted
    assert engine.evaluate("bash", "git log").action == "allow"  # persisted `always[]`, not the pattern


def test_grouping_key() -> None:
    from auoreo.core.approvals import Pending

    loop_future = None

    def mk(rid: str, perm: str, pat: str, tool: str = "bash") -> Pending:
        return Pending(rid, "s", tool, perm, pat, "", loop_future, [pat], [pat], "/w", "medium")  # type: ignore[arg-type]

    groups = grouped([mk("1", "bash", "ls"), mk("2", "edit", "/w/a", "edit"), mk("3", "bash", "pwd")])
    bash = next(g for g in groups if g["permission"] == "bash")
    assert (bash["count"], bash["first_pattern"], bash["last_pattern"]) == (2, "ls", "pwd")
    assert bash["request_ids"] == ["1", "3"] and bash["cwd_root"] == "/w"


# ---------------------------------------------------------------- unattended
def test_unattended_policy_defaults_deny() -> None:
    st = Settings()
    assert all(unattended_action(st, k) == "deny" for k in ("single_query", "cron", "platform"))
    st2 = Settings(approvals={"unattended": {"cron": "approve"}})
    assert unattended_action(st2, "cron") == "approve" and unattended_action(st2, "platform") == "deny"


async def test_executor_unattended_branch(services: Services, workdir: Path) -> None:
    rules = [R("bash", "*", "ask")]

    async def run(kind: str) -> str:
        ctx = ToolContext(
            session_id="s1",
            cwd=workdir,
            home=services.settings.home,
            harness=services.harness,
            services={"unattended": kind},
        )
        out = await services.coordinator.runner.executor.run(
            ToolCall("c1", "bash", json.dumps({"command": "echo unattended-ok"})),
            tools=services.harness.tools,
            ctx=ctx,
            engine=PermissionEngine(rules),
        )
        return out.result.content

    text = await run("cron")
    assert "auoreo config set approvals.unattended.cron approve" in text
    services.settings.approvals.unattended.cron = "approve"
    try:
        assert "unattended-ok" in await run("cron")
        assert "approvals.unattended.platform" in await run("platform")
    finally:
        services.settings.approvals.unattended.cron = "deny"


async def test_unattended_never_approves_sensitive(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(workdir))
    services.settings.approvals.unattended.single_query = "approve"
    try:
        target = workdir / ".ssh" / "config"
        ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home, harness=services.harness)
        out = await services.coordinator.runner.executor.run(
            ToolCall("c1", "write", json.dumps({"path": str(target), "content": "Host x\n"})),
            tools=services.harness.tools,
            ctx=ctx,
            engine=PermissionEngine([R("*", "*", "allow")]),
        )
    finally:
        services.settings.approvals.unattended.single_query = "deny"
    assert out.result.is_error and "never auto-approved" in out.result.content and not target.exists()


# --------------------------------------------------------------- shards + cache
def test_cache_invalidated_on_rule_changes() -> None:
    e = PermissionEngine([R("bash", "*", "ask")])
    assert e.evaluate("bash", "make").action == "ask"
    gen = e.generation
    e.session_rules.append(R("bash", "make", "allow"))  # in-place mutation is detected
    assert e.evaluate("bash", "make").action == "allow"
    e.rules = [R("bash", "*", "deny")]
    assert e.generation > gen
    assert e.evaluate("bash", "ls").action == "deny"
    e.grant_always("bash", "ls")
    assert e.evaluate("bash", "ls").action == "allow"
    # wildcard permissions interleave with shards in global order
    e2 = PermissionEngine([R("bash", "ls", "allow"), R("*", "*", "deny")])
    assert e2.evaluate("bash", "ls").action == "deny"
    # compound commands never match a narrow allow
    e3 = PermissionEngine([R("bash", "*", "ask"), R("bash", "ls*", "allow")])
    assert e3.evaluate("bash", "ls; curl x").action == "ask"


def test_evaluate_p50_under_1ms_with_5000_rules() -> None:
    rng = random.Random(7)
    perms = ["read", "edit", "webfetch", "mcp__x__y", "external_directory"]
    rules = [R("bash", "*", "ask")]
    for i in range(4999):
        if i % 10:
            rules.append(
                R("bash", f"tool{i} {rng.choice(['run', 'build', 'test'])}*", rng.choice(["allow", "deny"]))
            )
        else:
            rules.append(R(rng.choice(perms), f"/srv/p{i}/*", "allow"))
    e = PermissionEngine(rules)
    queries = [("bash", f"tool{rng.randrange(5000)} run x{n}") for n in range(400)]
    queries += [("bash", f"unknown-cmd {n}") for n in range(100)]
    queries += [("read", f"/srv/p{rng.randrange(5000)}/f{n}") for n in range(100)]
    e.evaluate("bash", "warmup")
    times = []
    for perm, pat in queries:
        t0 = time.perf_counter()
        e.evaluate(perm, pat)
        times.append(time.perf_counter() - t0)
    p50 = statistics.median(times) * 1000
    print(f"5k rules: p50 evaluate = {p50:.3f} ms (uncached)")
    assert p50 < 1.0
    t0 = time.perf_counter()
    for perm, pat in queries:
        e.evaluate(perm, pat)
    cached = (time.perf_counter() - t0) / len(queries) * 1000
    print(f"5k rules: mean evaluate = {cached:.4f} ms (cached)")
    assert cached < 1.0
