"""Context-window discovery tiers (spec §25.6 L968). No `litellm` here.

`resolve_window` tries, first hit wins: the routing group's pinned `max_input_tokens` →
per-deployment `i2.deployments[...].max_input_tokens` → `[1m]` marker → the provider's
model info (LiteLLM, injected) → a small known-family table → the probe default (tier[0]
of `probe_tiers` that is ≤ `probe_default`; warned once, never persisted). `min_context`
rejects a KNOWN window below the floor up front (user pins always win).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from auoreo.gateway.errors import GatewayError

log = logging.getLogger(__name__)
ONE_MILLION = 1_000_000
KNOWN: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"claude", re.I), 200_000),
    (re.compile(r"gemini-(1\.5|2|3)", re.I), 1_048_576),
    (re.compile(r"gpt-4\.1|gpt-5", re.I), 400_000),
    (re.compile(r"gpt-4o|o[134](-|$)", re.I), 128_000),
    (re.compile(r"llama-?3\.[1-3]|llama3\.[1-3]", re.I), 128_000),
    (re.compile(r"qwen3", re.I), 128_000),
    (re.compile(r"deepseek", re.I), 128_000),
    (re.compile(r"mistral-large|codestral", re.I), 128_000),
    (re.compile(r"kimi|moonshot", re.I), 256_000),
)
_warned: set[str] = set()


@dataclass(frozen=True)
class Window:
    tokens: int
    source: str  # pinned | deployment | 1m | model_info | table | probe

    @property
    def known(self) -> bool:
        return self.source != "probe"


def table_window(model: str) -> int | None:
    return next((w for rx, w in KNOWN if rx.search(model)), None)


def probe_default(tiers: list[int], default: int) -> int:
    """The largest probe tier not above `default` (cheapest-first discovery starts here)."""
    fitting = [t for t in tiers if t <= default]
    return max(fitting) if fitting else default


def resolve_window(
    model: str,
    deployments: list[str],
    *,
    pinned: int | None,
    per_deployment: Callable[[str], int | None],
    info: Callable[[str], int | None],
    tiers: list[int],
    default: int,
) -> Window:
    if pinned:
        return Window(pinned, "pinned")
    for dep in deployments:
        if w := per_deployment(dep):
            return Window(w, "deployment")
    if any("[1m]" in m for m in [model, *deployments]):
        return Window(ONE_MILLION, "1m")
    for dep in deployments:
        if w := info(dep):
            return Window(w, "model_info")
    for dep in deployments:
        if w := table_window(dep):
            return Window(w, "table")
    guess = probe_default(tiers, default)
    if model not in _warned:
        _warned.add(model)
        log.warning("context window of %s is unknown; assuming %d tokens (not persisted)", model, guess)
    return Window(guess, "probe")


def guard_min_context(model: str, window: Window, min_context: int) -> None:
    """Reject a known window below `min_context` (pinned windows are the user's call)."""
    if min_context and window.source not in {"pinned", "deployment", "probe"} and window.tokens < min_context:
        raise GatewayError(  # not CONTEXT: that code triggers overflow compaction
            "INVALID_REQUEST",
            f"{model} has a {window.tokens}-token context window, below i2.min_context={min_context}; "
            "pin models.<group>.max_input_tokens to use it anyway",
        )


__all__ = ["KNOWN", "Window", "guard_min_context", "probe_default", "resolve_window", "table_window"]
