"""G5a: per-run request overrides from `auoreo run` (`--bare`, `--variant`, `--thinking`).

Applied to the frozen `AgentSpec` at the start of a turn, so nothing else in the loop needs
to know about them:

- `bare`: the system prompt is the agent's own body; prompt sections and family overlays
  are left out (tools, rules and context stay).
- `variant`: shape requests with the `prompts/variants.yaml` entry that matches this id
  (e.g. `openai/gpt-5`) instead of the one matched from the model.
- `effort`: reasoning effort `low|medium|high|max` for this run (the bandit, when on, may
  still pick per its own policy).
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from auoreo.runtime.inbox import RunOptions

if TYPE_CHECKING:
    from auoreo.harness.orchestrator import AgentSpec
    from auoreo.harness.registry import Harness

EFFORTS = ("low", "medium", "high", "max")


class UnknownVariant(ValueError):
    """No variants.yaml entry matches the requested id."""


def find_variant(harness: Harness, name: str) -> object:
    from auoreo.gateway.pipeline import match_variant

    vf = harness.snapshot.variants
    found = match_variant(list(vf.manifest.variants), name) if vf is not None else None
    if found is None:
        raise UnknownVariant(f"no prompts/variants.yaml entry matches '{name}'")
    return found


def apply_run_options(harness: Harness, spec: AgentSpec, options: RunOptions | None) -> AgentSpec:
    if options is None or not (options.bare or options.variant or options.effort):
        return spec
    changes: dict[str, object] = {}
    if options.bare:
        changes["system_prompt"] = spec.agent.body.strip()
    if options.variant:
        changes["variant"] = find_variant(harness, options.variant)
    if options.effort:
        if options.effort not in EFFORTS:
            raise ValueError(f"effort must be one of {', '.join(EFFORTS)}")
        manifest = spec.agent.manifest.model_copy(update={"effort": options.effort})
        changes["agent"] = dataclasses.replace(spec.agent, manifest=manifest)
    return dataclasses.replace(spec, **changes)  # type: ignore[arg-type]
