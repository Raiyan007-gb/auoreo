"""Cheap-model route rule, the pure half only (M7 W1 Wave 2 design, gates R1-R3).

The route itself (running a turn's first step on the `small` group with a rerun on the
default model) is NOT built: the design builds it only if report gates R1 to R3 pass on real
data, else it moves to M8. This module holds the eligibility rule so `auoreo stats
--token-economy` can measure how many turns the route would take. Nothing in the runtime
calls it.
"""

from __future__ import annotations

from dataclasses import dataclass

from auoreo.policy.bandit import is_correction

RULE = "trivial"
MAX_CHARS = 400
MAX_HISTORY_TOKENS = 4000
ROUTE_MODEL = "small"
PLAN_AGENT = "planner"


@dataclass(frozen=True)
class RouteFacts:
    text: str
    agent: str
    history_tokens: int
    override: bool = False
    """A model override (session model, `--model`, `/model`) is set."""
    subagent: bool = False
    exact_fork: bool = False
    attachments: bool = False
    plan_mode: bool = False


@dataclass(frozen=True)
class RouteRule:
    groups: frozenset[str]
    """Configured routing groups (`settings.models`)."""
    model: str = ROUTE_MODEL
    max_chars: int = MAX_CHARS
    max_history_tokens: int = MAX_HISTORY_TOKENS
    agents: frozenset[str] = frozenset()
    """Agents the route may take; empty means any."""


def eligible(facts: RouteFacts, rule: RouteRule) -> str | None:
    """The rule name when the turn would be routed, else None."""
    blocked = (
        facts.override,
        facts.subagent,
        facts.exact_fork,
        facts.attachments,
        facts.plan_mode or facts.agent == PLAN_AGENT,
        is_correction(facts.text),
        len(facts.text) > rule.max_chars,
        facts.history_tokens > rule.max_history_tokens,
        bool(rule.agents) and facts.agent not in rule.agents,
        rule.model not in rule.groups,
    )
    return None if any(blocked) else RULE


__all__ = ["RULE", "RouteFacts", "RouteRule", "eligible"]
