"""I1 §19.6: a subagent inherits its parent's `deny ∪ external_directory` rules (agent deny
rules and session rules such as `run --deny`), and leaf subagents lose `todo`/`todowrite`."""

from __future__ import annotations

from pathlib import Path

import pytest

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.harness.manifests import Rule
from auoreo.teams.ceiling import LEAF_DENIED, CeilingEngine, inherited
from auoreo.teams.service import TeamsService
from tests.teams_helpers import RoutedGateway, new_parent, write_agent


@pytest.fixture
def gateway() -> RoutedGateway:
    return RoutedGateway()


def test_inherited_keeps_only_deny_and_external_directory() -> None:
    parent = PermissionEngine(
        [
            Rule(permission="bash", pattern="*", action="allow"),
            Rule(permission="webfetch", pattern="*", action="deny"),
            Rule(permission="external_directory", pattern="/tmp/*", action="allow"),
        ],
        session_rules=[Rule(permission="bash", pattern="curl*", action="deny")],
    )
    rules, denied = inherited(parent)
    assert [(r.permission, r.pattern, r.action) for r in rules] == [
        ("webfetch", "*", "deny"),
        ("external_directory", "/tmp/*", "allow"),
        ("bash", "curl*", "deny"),
    ]
    assert denied == frozenset() and inherited(None) == ([], frozenset())
    assert "todo" in LEAF_DENIED and "todowrite" in LEAF_DENIED


async def test_child_engine_inherits_parent_denials(services: Services, workdir: Path) -> None:
    write_agent(
        workdir,
        "loose",
        "permissions:\n  - {permission: bash, pattern: '*', action: allow}\n"
        "  - {permission: webfetch, pattern: '*', action: allow}\n",
    )
    services.harness.reload()
    parent = await new_parent(services)
    prt = await services.coordinator.runtime(parent)
    prt.engine = PermissionEngine(
        [Rule(permission="webfetch", pattern="*", action="deny")],  # parent agent deny rule
        session_rules=[Rule(permission="bash", pattern="curl*", action="deny")],  # `run --deny`
    )
    teams = services.ext["teams"]
    assert isinstance(teams, TeamsService)
    m = await teams.spawn(parent, agent="loose", prompt="hi", name="c", background=False)
    engine = (await services.coordinator.runtime(m.session_id)).engine
    assert isinstance(engine, CeilingEngine)
    # after the child's turn the runner refreshed `rules` from its own (permissive) manifest,
    # but the inherited denials live in session_rules and still win
    assert engine.evaluate("bash", "ls", "ask").action == "allow"
    assert engine.evaluate("bash", "curl https://evil.example", "ask").action == "deny"
    assert engine.evaluate("webfetch", "https://x.example", "ask").action == "deny"
    assert engine.evaluate("todo", "*", "ask").action == "deny"  # leaf denial
