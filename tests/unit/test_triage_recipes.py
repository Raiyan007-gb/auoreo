"""M7 W3 wave 2: the bundled triage recipes load disabled, and the `triage` agent's explicit
permissions deny every permission but its own tools (a `permission: "*"` rule matches every
permission name), so its behaviour does not depend on `approvals.unattended.*`."""

from __future__ import annotations

import pytest

from auoreo.automation.templating import first_failing
from auoreo.automation.webhooks import build_payload
from auoreo.core.permissions import PermissionEngine, hidden_by_rules
from auoreo.core.services import Services
from auoreo.harness.orchestrator import build_spec

ALLOW = {"suggest", "kanban_list", "kanban_show", "read", "grep", "glob", "tool_search"}
ISSUE = {
    "action": "opened",
    "issue": {"number": 42, "title": "Crash", "body": "b", "html_url": "https://github.com/acme/app/issues/42",
              "user": {"login": "ana"}},
    "repository": {"full_name": "acme/app"},
}  # fmt: skip


def test_definitions_load_disabled(services: Services) -> None:
    hook = services.harness.webhooks["issue-to-kanban"].manifest
    job = services.harness.jobs["triage"].manifest
    agent = services.harness.agent("triage").manifest
    assert hook.enabled is False and hook.agent == "triage" and hook.provider == "github"
    assert hook.events == ["issues"] and hook.max_steps == 8 and hook.deliver == ["log"]
    assert job.enabled is False and job.agent == "triage" and job.catch_up is False and job.timeout_s == 300
    assert agent.memory is False and agent.max_steps == 12 and set(agent.tools.allow) == ALLOW


def test_agent_tools_equal_the_allowlist(services: Services) -> None:
    spec = build_spec(services.harness, "triage", "default", "manual")
    assert {t.name for t in spec.tools} | {t.name for t in spec.deferred} == ALLOW


@pytest.mark.parametrize(
    "permission",
    ["bash", "edit", "kanban_create", "delegate_task", "memory", "web_fetch", "mcp__gh__create_issue",
     "external_directory", "sandbox_escalate", "gh_pr_create", "plugin_install", "ssh"],
)  # fmt: skip
def test_star_rule_denies_every_other_permission(services: Services, permission: str) -> None:
    """Verified claim: `permission: "*"` compiles to `.*` (`core/permissions._wild`) and is
    matched against every permission name, so even a tool whose default is `allow` is denied."""
    rules = build_spec(services.harness, "triage", "default", "manual").rules
    engine = PermissionEngine(list(rules))
    for default in ("allow", "ask"):
        d = engine.evaluate(permission, "anything at all", default, interactive=False)  # type: ignore[arg-type]
        assert d.action == "deny" and d.rule is not None and d.rule.permission == "*", (permission, d)
    assert hidden_by_rules(permission, rules)


def test_own_tools_are_allowed_and_secret_reads_stay_denied(services: Services) -> None:
    rules = build_spec(services.harness, "triage", "default", "manual").rules
    engine = PermissionEngine(list(rules))
    for permission in ("suggest", "kanban_list", "kanban_show", "tool_search"):
        assert engine.evaluate(permission, "*", "ask", interactive=False).action == "allow"
    assert engine.evaluate("read", "/work/src/app.py", "ask", interactive=False).action == "allow"
    for path in (
        "/work/.env",
        "/work/.env.local",
        "/home/x/.auoreo/secrets.db",
        "/home/x/.auoreo/.secrets.key",
    ):
        assert engine.evaluate("read", path, "allow", interactive=False).action == "deny", path


def test_webhook_prompt_hands_over_the_dedupe_key_and_filters_opened(services: Services) -> None:
    manifest = services.harness.webhooks["issue-to-kanban"].manifest
    built = build_payload(manifest, ISSUE, "issues", "d-1")
    assert 'dedupe_key "acme/app#42"' in built["prompt"]
    assert 'ref: "https://github.com/acme/app/issues/42"' in built["prompt"]
    assert built["delivery_target"] == "acme/app#42" and built["agent"] == "triage"
    assert first_failing(manifest.filters, ISSUE, services.settings.home) is None
    edited = ISSUE | {"action": "edited"}
    assert first_failing(manifest.filters, edited, services.settings.home) is not None
