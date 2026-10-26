"""Permission ceiling for child agents: effective mode = min(parent, child) on the
ladder `manual < smart < acceptEdits < yolo` (`plan` is sticky: it caps everything).

Depth-derived roles (spec §16.3): a delegated child is an `orchestrator` iff
`teams_orchestrator_enabled` and its depth < 2, else a `leaf`. A leaf's engine denies the
`LEAF_DENIED` tools (by their permission names) as a floor, so the executor refuses them even
when a rule, mode or `tool_call` would allow them; they are also hidden from its tool list."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, cast

from auoreo.core.permissions import Decision, Mode, PermissionEngine

LADDER: dict[str, int] = {"manual": 0, "smart": 1, "acceptEdits": 2, "yolo": 3}
LEAF_DENIED: tuple[str, ...] = (
    "delegate_task",
    "resume_agent",
    "clarify",
    "ask_user_question",
    "memory",
    "send_message",
    "cronjob",
)
ORCHESTRATOR_MAX_DEPTH = 2


def role_for(orchestrator_enabled: bool, depth: int) -> str:
    """`orchestrator` iff enabled and depth < 2, else `leaf` (the model's `role` is ignored)."""
    return "orchestrator" if orchestrator_enabled and depth < ORCHESTRATOR_MAX_DEPTH else "leaf"


def leaf_permissions(tools: Mapping[str, Any], names: Iterable[str] = LEAF_DENIED) -> frozenset[str]:
    """Permission names of the leaf-denied tools (a tool's `permission`, else its name)."""
    out: set[str] = set()
    for name in names:
        out.add(name)
        tool = tools.get(name)
        if tool is not None:
            out.add(str(tool.manifest.permission_name))
    return frozenset(out)


def clamp(requested: str, ceiling: str) -> Mode:
    """The weaker of two modes. `plan` on either side wins (it only removes power)."""
    if "plan" in (requested, ceiling):
        return "plan"
    req, cap = LADDER.get(requested, 0), LADDER.get(ceiling, 0)
    return cast(Mode, requested if req <= cap else ceiling)


class CeilingEngine(PermissionEngine):
    """A `PermissionEngine` whose `mode` can never be raised above `ceiling`.

    The runner keeps an existing engine object between turns and re-assigns `mode` from
    the agent manifest (`AgentRunner._engine`); the clamping setter makes that refresh
    respect the parent's ceiling without any runtime changes. `denied` permission names
    (a leaf's) are refused before anything else is evaluated."""

    def __init__(self, ceiling: str, *args: Any, denied: frozenset[str] = frozenset(), **kwargs: Any) -> None:
        self.ceiling = ceiling
        self.denied = denied
        super().__init__(*args, **kwargs)

    @property
    def mode(self) -> Mode:
        return self._mode

    @mode.setter
    def mode(self, value: Mode) -> None:
        self._mode: Mode = clamp(value, self.ceiling)

    def evaluate(self, permission: str, pattern: str, default: Any = "ask", **kwargs: Any) -> Decision:
        if permission in self.denied:
            return Decision("deny", "floor", note=f"'{permission}' is not available to leaf subagents")
        return super().evaluate(permission, pattern, default, **kwargs)
