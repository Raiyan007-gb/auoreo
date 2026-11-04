"""Per-turn budgets and stats (spec §8.7).

`IterationBudget` counts model calls: a parent turn gets `loop.max_iterations` (500), a
subagent turn (a session with a parent) `loop.delegation_max_iterations` (50). Every model
call of a step consumes one unit; programmatic calls do not (OTK escalation/resume
nudges, the empty-response retry, the overflow-compaction retry). A step is refunded
when *every* tool call it made failed before dispatch for a model-side shape reason
(`FREE_FAILURE_PREFIXES`: unknown tool, non-JSON or schema-invalid arguments) — nothing
ran, so the call bought nothing; the agent's `max_steps` still bounds such loops.

On exhaustion (iterations, wall clock `run_budget_seconds` or completion tokens
`token_budget`) the loop makes exactly ONE grace call with `tool_choice=none` and a
wrap-up note, then stops `blocked` / `BUDGET_EXHAUSTED`. Before that a single latched
wrap-up nudge is sent at 80 % wall clock / 90 % tokens.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from auoreo.core.usage import cache_hit_ratio

if TYPE_CHECKING:
    from auoreo.core.settings_models import LoopSettings
    from auoreo.tools.executor import ToolOutcome

OUT_OF_BAND = "[OUT-OF-BAND]"
"""Marker on messages the harness (not the user) injects into a turn (protocol constant)."""
FREE_FAILURE_PREFIXES = (
    "unknown tool '",
    "arguments are not valid JSON",
    "arguments must be a JSON object",
    "invalid arguments at ",
    "InvalidArgumentsError: ",  # G4a: typed stage-1 failures (lossless parse + schema)
)
WALL_WRAPUP_AT = 0.8
TOKEN_WRAPUP_AT = 0.9
DIMINISHING_COUNT = 3
DIMINISHING_DELTA = 500
GRACE_NOTE = (
    f"{OUT_OF_BAND} The run budget ({{why}}) is exhausted. This is your last reply: do not "
    "call tools. Summarize what you did, what is left, and answer now."
)
WRAPUP_NOTE = (
    f"{OUT_OF_BAND} {{pct}}% of this run's {{what}} budget is used. Wrap up: finish the "
    "current step, avoid starting new work, and prepare your final answer."
)
CONTINUE_NOTE = (
    f"{OUT_OF_BAND} You have used {{used}} of {{budget}} output tokens for this task. "
    "Keep working: continue with the next part instead of stopping early."
)


@dataclass
class IterationBudget:
    total: int
    used: int = 0

    def consume(self) -> None:
        self.used += 1

    def refund(self) -> None:
        self.used = max(0, self.used - 1)

    @property
    def exhausted(self) -> bool:
        return self.used >= self.total


@dataclass
class TurnBudget:
    """Iteration + wall-clock + token budgets of one turn, with turn stats for `turn.summary`."""

    iterations: IterationBudget
    run_budget_seconds: float | None = None
    token_budget: int | None = None
    diminishing_returns: bool = True
    completion_threshold: float = 0.0
    started: float = field(default_factory=time.monotonic)
    steps: int = 0
    model_calls: int = 0
    tools: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    grace_used: bool = False
    _wrapped: set[str] = field(default_factory=set)
    _deltas: deque[int] = field(default_factory=lambda: deque(maxlen=DIMINISHING_COUNT))
    _continue_from: int | None = None

    @classmethod
    def for_turn(cls, cfg: LoopSettings, *, subagent: bool) -> TurnBudget:
        total = cfg.delegation_max_iterations if subagent else cfg.max_iterations
        return cls(
            IterationBudget(max(1, total)),
            cfg.run_budget_seconds,
            cfg.token_budget,
            cfg.diminishing_returns,
            cfg.completion_threshold,
        )

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def consume_step(self, *, grace: bool = False) -> None:
        """A step's model call: one budget unit (the grace call is beyond the budget)."""
        self.steps += 1
        if grace:
            self.grace_used = True
        else:
            self.iterations.consume()

    def record(
        self, prompt: int, completion: int, calls: int = 1, *, cached: int = 0, cache_write: int = 0
    ) -> None:
        self.model_calls += calls
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.cached_tokens += cached
        self.cache_write_tokens += cache_write

    def exhausted(self) -> str | None:
        """Why the budget is spent (`iterations` / `wall_clock` / `tokens`), else None."""
        if self.iterations.exhausted:
            return f"iterations: {self.iterations.used}/{self.iterations.total} model calls"
        if self.run_budget_seconds is not None and self.elapsed() >= self.run_budget_seconds:
            return f"wall_clock: {self.run_budget_seconds:g}s"
        if self.token_budget is not None and self.completion_tokens >= self.token_budget:
            return f"tokens: {self.completion_tokens}/{self.token_budget}"
        return None

    def wrapup_note(self) -> str | None:
        """The latched wrap-up nudge, returned once per budget kind when its threshold passes."""
        due: list[tuple[str, int]] = []
        if self.run_budget_seconds is not None and self.elapsed() >= WALL_WRAPUP_AT * self.run_budget_seconds:
            due.append(("time", int(WALL_WRAPUP_AT * 100)))
        if self.token_budget is not None and self.completion_tokens >= TOKEN_WRAPUP_AT * self.token_budget:
            due.append(("token", int(TOKEN_WRAPUP_AT * 100)))
        for what, pct in due:
            if what not in self._wrapped:
                self._wrapped.add(what)
                return WRAPUP_NOTE.format(pct=pct, what=what)
        return None

    def continue_note(self) -> str | None:
        """Token budget below `completion_threshold` at a text stop: a keep-going nudge,
        unless the last DIMINISHING_COUNT continuations each added < DIMINISHING_DELTA tokens."""
        if not self.token_budget or self.completion_threshold <= 0:
            return None
        if self._continue_from is not None:
            self._deltas.append(self.completion_tokens - self._continue_from)
        if self.completion_tokens >= self.completion_threshold * self.token_budget:
            return None
        if (
            self.diminishing_returns
            and len(self._deltas) == DIMINISHING_COUNT
            and all(d < DIMINISHING_DELTA for d in self._deltas)
        ):
            return None
        self._continue_from = self.completion_tokens
        return CONTINUE_NOTE.format(used=self.completion_tokens, budget=self.token_budget)

    def summary(self, status: str) -> dict[str, Any]:
        return {
            "status": status,
            "steps": self.steps,
            "model_calls": self.model_calls,
            "tools": self.tools,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_tokens": self.cached_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cache_hit_ratio": cache_hit_ratio(self.cached_tokens, self.prompt_tokens),
            "duration_ms": int(self.elapsed() * 1000),
            "budget": {"used": self.iterations.used, "total": self.iterations.total},
        }


def all_free(outcomes: list[ToolOutcome]) -> bool:
    """True when every call failed before dispatch for a model-side shape reason."""
    return bool(outcomes) and all(
        o.result.is_error and o.result.content.startswith(FREE_FAILURE_PREFIXES) for o in outcomes
    )
