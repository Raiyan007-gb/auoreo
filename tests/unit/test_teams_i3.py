"""I3 teams closure: exact fork, in-tool staleness, coordinator allowlist + synthetic_output,
handoff schema, control messages, allowedTools / inherit_mcp_toolsets, subagent ids, usage
attribution (spec §16.1–§16.5, §24.10, §25.11, §25.15)."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from auoreo.core.db import AuditRow, MailboxRow, TeamMemberRow, UsageRow
from auoreo.core.services import Services
from auoreo.core.usage_attrib import by_subagent
from auoreo.gateway.fake import Scripted
from auoreo.gateway.types import LLMRequest
from auoreo.teams.control_msgs import PinnedCeilingEngine
from auoreo.teams.fork import PLACEHOLDER
from auoreo.teams.format import HANDOFF_SCHEMA, check_value
from auoreo.teams.service import TeamsError, TeamsService
from auoreo.teams.spawn_i3 import denied_permissions, mcp_deny
from tests.conftest import make_tool
from tests.teams_helpers import RoutedGateway, call_tool, new_parent, tool_ctx, turn


@pytest.fixture
def gateway() -> RoutedGateway:
    return RoutedGateway()


def teams_of(svc: Services) -> TeamsService:
    teams = svc.ext["teams"]
    assert isinstance(teams, TeamsService)
    return teams


def tool_names(req: LLMRequest) -> list[str]:
    return [str((t.get("function") or {}).get("name")) for t in req.tools]


async def events_of(svc: Services, sid: str) -> list[tuple[str, dict[str, Any]]]:
    evs = await svc.events.replay(0, frozenset({f"session:{sid}"}), limit=100_000)
    return [(e.type, e.data) for e in evs]


# ---------------------------------------------------------------- §16.2 exact fork
async def test_exact_fork_reuses_parent_prefix_byte_for_byte(
    services: Services, gateway: RoutedGateway
) -> None:
    args = {"goal": "FORKED-TASK count files", "name": "twin", "mode": "exact"}
    gateway.route(
        "PARENT-EXACT",
        Scripted(tool_calls=[("delegate_task", args)]),
        Scripted(text="fork result"),  # the child's first request routes by the shared first message
        Scripted(text="parent done"),
    )
    parent = await new_parent(services)
    result = await turn(services, parent, "PARENT-EXACT please fork")
    assert result.status == "completed" and result.text == "parent done"
    reqs = gateway.requests_for("PARENT-EXACT")
    first, child = reqs[0], reqs[1]
    assert child.metadata["session_id"] != parent
    assert child.messages[0] == first.messages[0]  # byte-identical system prompt
    assert child.tools == first.tools  # exact tool list
    assert child.model == first.model  # model=inherit
    assert child.messages[: len(first.messages)] == first.messages  # cacheable prefix
    rest = child.messages[len(first.messages) :]
    assert rest[0]["role"] == "assistant" and rest[0]["tool_calls"][0]["function"]["name"] == "delegate_task"
    assert rest[1] == {"role": "tool", "tool_call_id": rest[0]["tool_calls"][0]["id"], "content": PLACEHOLDER}
    assert "<fork-directive>" in rest[2]["content"] and "FORKED-TASK" in rest[2]["content"]
    fork_ev = [d for t, d in await events_of(services, parent) if t == "subagent.fork"]
    assert len(fork_ev) == 1 and re.fullmatch(r"[0-9a-f]{64}", fork_ev[0]["cache_key"])
    # the parent's last request carries the child's result
    assert "fork result" in json.dumps(reqs[-1].messages[-1])


async def test_exact_fork_cache_miss_falls_back_to_summary(
    services: Services, gateway: RoutedGateway
) -> None:
    gateway.route("CHILD-MISS", Scripted(text="ok"))
    teams = teams_of(services)
    parent = await new_parent(services)
    kid = await teams.spawn(
        parent, agent="default", prompt="CHILD-MISS go", name="miss", background=False, mode="exact"
    )
    assert kid.status == "completed" and kid.exact is None
    assert any(t == "subagent.fork_cache_miss" for t, _ in await events_of(services, parent))
    with pytest.raises(TeamsError, match="cannot fork again"):  # boilerplate recursion guard
        await teams.spawn(
            kid.session_id, agent="default", prompt="x", name="deeper", background=False, mode="fork"
        )


# ---------------------------------------------------------------- §16.1 stale idle vs in-tool
async def test_stale_limit_depends_on_open_tool_call(
    services: Services, gateway: RoutedGateway, workdir: Path
) -> None:
    cfg, i3 = services.settings.teams2.delegation, services.settings.i3.delegation
    cfg.poll_s, cfg.heartbeat_s = 0.05, 10.0
    i3.stale_idle_s, i3.stale_in_tool_s = 0.4, 30.0
    # well past stale_idle_s, inside the tool
    make_tool(workdir / ".auoreo", "slow_probe", "import asyncio; await asyncio.sleep(1.2); return 'slow ok'")
    services.harness.reload()
    gateway.route("CHILD-INTOOL", Scripted(tool_calls=[("slow_probe", {})]), Scripted(text="survived"))
    teams = teams_of(services)
    parent = await new_parent(services)
    kid = await teams.spawn(
        parent, agent="default", prompt="CHILD-INTOOL go", name="intool", background=False
    )
    assert kid.status == "completed" and kid.text == "survived", kid.error

    gate = asyncio.Event()  # idle outside any tool: the 0.4 s idle limit applies
    gateway.route("CHILD-IDLE", Scripted(text="never"), gate=gate)
    kid2 = await teams.spawn(parent, agent="default", prompt="CHILD-IDLE go", name="idle", background=False)
    assert kid2.status == "failed" and kid2.error and kid2.error["code"] == "STALE"
    assert "outside tool calls" in kid2.error["message"]
    gate.set()


# ---------------------------------------------------------------- §16.5 coordinator + synthetic_output
def test_coordinator_allowlist_is_exact(services: Services) -> None:
    allow = services.harness.agent("coordinator").manifest.tools.allow
    assert set(allow) == {"delegate_task", "send_message", "stop_task", "synthetic_output"}
    assert "synthetic_output" in services.harness.tools
    assert services.harness.agent("swarm_lead").manifest.tools.allow  # the swarm keeps its toolbox


async def test_synthetic_output_validates_and_becomes_the_child_answer(
    services: Services, gateway: RoutedGateway
) -> None:
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    gateway.route(
        "COORD-SYN",
        Scripted(tool_calls=[("synthetic_output", {"output": {"n": "x"}})]),
        Scripted(tool_calls=[("synthetic_output", {"output": {"n": 3}})]),
        Scripted(text="recorded"),
    )
    teams = teams_of(services)
    parent = await new_parent(services)
    kid = await teams.spawn(
        parent,
        agent="coordinator",
        prompt="COORD-SYN go",
        name="coord",
        background=False,
        output_schema=schema,
    )
    assert kid.status == "completed", kid.error
    assert json.loads(kid.text) == {"n": 3}
    tool_results = [m for m in gateway.requests_for("COORD-SYN")[1].messages if m.get("role") == "tool"]
    assert "TOOL_OUTPUT_SCHEMA_MISMATCH" in str(tool_results[-1]["content"])


# ---------------------------------------------------------------- §25.11 handoff schema
def test_handoff_schema_shape() -> None:
    ok = {"purpose": "fix", "files": ["src/a.py:12"], "done": True, "commit_hash": "abc1234", "summary": "s"}
    assert check_value(ok, HANDOFF_SCHEMA) is None
    assert check_value({**ok, "summary": "x" * 81}, HANDOFF_SCHEMA) is not None
    assert check_value({**ok, "files": ["src/a.py"]}, HANDOFF_SCHEMA) is not None
    assert check_value({k: v for k, v in ok.items() if k != "done"}, HANDOFF_SCHEMA) is not None


async def test_handoff_child_retries_once_and_gets_usage(services: Services, gateway: RoutedGateway) -> None:
    good = {"purpose": "p", "files": ["a.py:1"], "done": True, "summary": "did it"}
    gateway.route("CHILD-HAND", Scripted(text="plain prose"), Scripted(text=json.dumps(good)))
    teams = teams_of(services)
    parent = await new_parent(services)
    kid = await teams.spawn(
        parent, agent="default", prompt="CHILD-HAND go", name="hand", background=False, handoff=True
    )
    assert kid.status == "completed", kid.error
    assert kid.handoff_data is not None and kid.handoff_data["summary"] == "did it"
    assert "usage" in kid.handoff_data and "prompt_tokens" in kid.handoff_data["usage"]
    assert any(t == "subagent.retry" for t, _ in await events_of(services, parent))


# ---------------------------------------------------------------- §25.11 control messages
async def test_control_messages_leader_to_worker_only(services: Services, gateway: RoutedGateway) -> None:
    gateway.route("CHILD-CTL", Scripted(text="ok"))
    teams = teams_of(services)
    parent = await new_parent(services)
    kid = await teams.spawn(parent, agent="default", prompt="CHILD-CTL go", name="ctl", background=False)
    ctx = tool_ctx(services, parent)
    rules = [{"permission": "bash", "pattern": "ls*", "action": "allow"}]
    text, err = await call_tool(
        services,
        ctx,
        "send_message",
        {"to": "ctl", "message": "-", "control": {"type": "team_permission_update", "rules": rules}},
    )
    assert not err, text
    engine = services.coordinator.engine_for(kid.session_id)
    assert engine is not None and any(
        r.permission == "bash" and r.pattern == "ls*" for r in engine.session_rules
    )

    text, err = await call_tool(  # leaf-denied names cannot be re-allowed
        services,
        ctx,
        "send_message",
        {
            "to": "ctl",
            "message": "-",
            "control": {
                "type": "team_permission_update",
                "rules": [{"permission": "delegate", "action": "allow"}],
            },
        },
    )
    assert err and "ceiling" in text
    text, err = await call_tool(
        services,
        ctx,
        "send_message",
        {"to": "ctl", "message": "-", "control": {"type": "mode_set_request", "mode": "yolo"}},
    )
    assert not err and "mode set to manual" in text  # clamped to the parent's manual ceiling
    assert isinstance(engine, PinnedCeilingEngine) and engine.pinned == "yolo"
    engine.mode = "smart"  # the runner's per-turn refresh does not undo the pin (ceiling still wins)
    assert engine.mode == "manual"

    # worker-originated: the child addressing its parent is rejected and audited
    wctx = tool_ctx(services, kid.session_id)
    text, err = await call_tool(
        services,
        wctx,
        "send_message",
        {"to": "lead", "message": "-", "control": {"type": "mode_set_request", "mode": "yolo"}},
    )
    assert err and "leader→worker only" in text
    async with services.db.session() as s:
        audit = [
            r for r in (await s.execute(select(AuditRow))).scalars() if r.action == "team.mode_set_request"
        ]
        types = [m.type for m in (await s.execute(select(MailboxRow))).scalars()]
    assert {"team_permission_update", "mode_set_request"} <= set(types)
    assert {r.decision for r in audit} == {"allow", "deny"}


# ---------------------------------------------------------------- §25.11 allowedTools / MCP inheritance
async def test_allowed_tools_replace_the_child_tool_list(services: Services, gateway: RoutedGateway) -> None:
    gateway.route("CHILD-ALLOW", Scripted(text="ok"))
    teams = teams_of(services)
    parent = await new_parent(services)
    with pytest.raises(TeamsError, match="unknown tools"):
        await teams.spawn(
            parent, agent="default", prompt="x", name="bad", background=False, allowed_tools=["nope_tool"]
        )
    kid = await teams.spawn(
        parent,
        agent="default",
        prompt="CHILD-ALLOW go",
        name="narrow",
        background=False,
        allowed_tools=["read", "glob"],
    )
    assert kid.status == "completed"
    names = tool_names(gateway.requests_for("CHILD-ALLOW")[0])
    assert set(names) <= {"read", "glob"} and names
    engine = services.coordinator.engine_for(kid.session_id)
    assert engine is not None and engine.evaluate("bash", "ls").action == "deny"


def test_mcp_inheritance_filters() -> None:
    class T:
        def __init__(self, perm: str) -> None:
            self.manifest = type("M", (), {"permission_name": perm})()

    tools = {"read": T("read"), "mcp__gh__issues": T("mcp__gh__issues"), "mcp__db__q": T("mcp__db__q")}
    assert mcp_deny(tools, False) == ["mcp__*"]
    assert mcp_deny(tools, True) == []
    assert mcp_deny(tools, ["gh"]) == ["mcp__db__q"]
    assert denied_permissions(tools, None, ["mcp__*"]) == {"mcp__gh__issues", "mcp__db__q"}
    assert denied_permissions(tools, ["read"], []) == {"mcp__gh__issues", "mcp__db__q"}


# ---------------------------------------------------------------- §16.4 / §25.15 ids, members, usage
async def test_subagent_ids_member_metadata_and_usage_attribution(
    services: Services, gateway: RoutedGateway
) -> None:
    gateway.route("CHILD-USE", Scripted(text="a"), Scripted(text="b"))
    teams = teams_of(services)
    parent = await new_parent(services)
    k1 = await teams.spawn(parent, agent="planner", prompt="CHILD-USE one", name="one", background=False)
    k2 = await teams.spawn(parent, agent="default", prompt="CHILD-USE two", name="two", background=False)
    assert k1.subagent_id.startswith("sa-1-") and k2.subagent_id.startswith("sa-2-")
    async with services.db.session() as s:
        rows = {r.name: r for r in (await s.execute(select(TeamMemberRow))).scalars()}
        usage = list(
            (await s.execute(select(UsageRow).where(UsageRow.session_id == k1.session_id))).scalars()
        )
    assert rows["one"].agent_type == "planner" and rows["one"].subagent_id == k1.subagent_id
    assert rows["one"].color and rows["one"].cwd == str(services.cwd)
    assert usage and all(
        (u.parent_session_id, u.subagent_id, u.depth, u.task_name) == (parent, k1.subagent_id, 1, "one")
        for u in usage
    )
    folded = {r["subagent_id"]: r for r in await by_subagent(services.db, 1)}
    assert folded[k1.subagent_id]["task_name"] == "one" and folded[k2.subagent_id]["calls"] >= 1
    live = (
        services.settings.home / "sessions" / parent / "delegation" / "live" / k1.subagent_id / "task-1.log"
    )
    assert live.is_file()

    ctx = tool_ctx(services, parent)
    text, err = await call_tool(
        services, ctx, "delegate_task", {"action": "stop", "subagent_id": k2.subagent_id}
    )
    assert not err and "two" in text


def test_stats_by_subagent_cli() -> None:
    from auoreo.cli.app import app as cli

    r = CliRunner().invoke(cli, ["stats", "--by-subagent", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["by_subagent"] == []
