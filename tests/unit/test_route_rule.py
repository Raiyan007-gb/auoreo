"""The cheap-model route rule, pure half only (M7 W1 Wave 2; the route itself is not built).

`auoreo stats --token-economy` uses it to count the turns the route would take (gates R1-R3).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from auoreo.policy.route import RULE, RouteFacts, RouteRule, eligible

RULE_SET = RouteRule(groups=frozenset({"default", "small"}))
OK = RouteFacts(text="what does HTTP 409 mean?", agent="build", history_tokens=100)


def test_a_short_fresh_question_is_eligible() -> None:
    assert eligible(OK, RULE_SET) == RULE == "trivial"


@pytest.mark.parametrize(
    "facts",
    [
        replace(OK, override=True),
        replace(OK, subagent=True),
        replace(OK, exact_fork=True),
        replace(OK, attachments=True),
        replace(OK, plan_mode=True),
        replace(OK, agent="planner"),
        replace(OK, text="no, that is wrong"),
        replace(OK, text="x" * 401),
        replace(OK, history_tokens=4001),
    ],
)
def test_each_condition_blocks_the_route(facts: RouteFacts) -> None:
    assert eligible(facts, RULE_SET) is None


def test_limits_are_inclusive() -> None:
    assert eligible(replace(OK, text="x" * 400, history_tokens=4000), RULE_SET) == RULE


def test_agent_list_and_missing_group() -> None:
    assert eligible(OK, replace(RULE_SET, agents=frozenset({"other"}))) is None
    assert eligible(OK, replace(RULE_SET, agents=frozenset({"build"}))) == RULE
    assert eligible(OK, RouteRule(groups=frozenset({"default"}))) is None
