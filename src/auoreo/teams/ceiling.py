"""Permission ceiling for child agents: effective mode = min(parent, child) on the
ladder `manual < smart < acceptEdits < yolo` (`plan` is sticky: it caps everything)."""

from __future__ import annotations

from typing import Any, cast

from auoreo.core.permissions import Mode, PermissionEngine

LADDER: dict[str, int] = {"manual": 0, "smart": 1, "acceptEdits": 2, "yolo": 3}


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
    respect the parent's ceiling without any runtime changes."""

    def __init__(self, ceiling: str, *args: Any, **kwargs: Any) -> None:
        self.ceiling = ceiling
        super().__init__(*args, **kwargs)

    @property
    def mode(self) -> Mode:
        return self._mode

    @mode.setter
    def mode(self, value: Mode) -> None:
        self._mode: Mode = clamp(value, self.ceiling)
