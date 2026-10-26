"""Aux model routing (spec §10.7, §24.4). No `litellm` here.

`resolve_aux_model(settings, task)` picks the model for a background call:
1. `auxiliary.tasks[<task>]` (explicit pin; never second-guessed),
2. the first `auxiliary.text_chain` (or `vision_chain`) entry whose credentials exist and,
   in `aux_probe_mode: lazy`, that has not failed within `probe_ttl_s`,
3. the `small` routing group when configured,
4. the main model (session override → settings → `default`).
It never raises. Credential checks go through the gateway (`has_credentials`), so this
module stays provider-agnostic.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from auoreo.gateway.resolve import expand_alias, resolve_model

if TYPE_CHECKING:
    from auoreo.core.settings import Settings

AUX_TASKS: tuple[str, ...] = ("title", "compaction", "guardian", "hyde", "translate", "summary")
BACKGROUND_PURPOSES: frozenset[str] = frozenset({*AUX_TASKS, "curator", "vision", "memory_query"})
"""`metadata.purpose` values that fail fast (no retry, no 529 fallback streak)."""

_failures: dict[str, float] = {}


def note_failure(model: str) -> None:
    """Remember that an aux call on `model` failed (used by `aux_probe_mode: lazy`)."""
    _failures[model] = time.monotonic()


def clear_failures() -> None:
    _failures.clear()


def _recently_failed(model: str, ttl: float) -> bool:
    at = _failures.get(model)
    return at is not None and time.monotonic() - at < ttl


def _free(model: str) -> str:
    return model if not model.startswith("openrouter/") or model.endswith(":free") else f"{model}:free"


def _usable(settings: Settings, entry: str, gateway: Any, vision: bool) -> bool:
    has = getattr(gateway, "has_credentials", None)
    group = settings.models.get(entry)
    deployments = list(group.models) if group else [entry]
    if group is not None and not deployments:
        return False
    if has is not None and not any(has(d) for d in deployments):
        return False
    return not (vision and gateway is not None and not gateway.supports_vision(entry))


def resolve_aux_model(
    settings: Settings,
    task: str,
    *,
    vision: bool = False,
    gateway: Any = None,
    main: str | None = None,
) -> str:
    """The model (routing group or provider id) for aux `task`; see the module docstring.

    `main` is the session's model (override), used for the `main` chain entry and as the
    last resort. `gateway` enables credential/vision checks; without it every entry counts
    as usable."""
    aux = settings.auxiliary
    pinned = aux.tasks.get(task)  # type: ignore[call-overload]
    if pinned:
        return expand_alias(settings, pinned)
    main_model = resolve_model(settings, session_override=main)
    chain = list(aux.vision_chain if vision else aux.text_chain)
    if (ort := settings.i2.openrouter).prefer_fast_model:  # --- I2: live-catalog recency sort
        from auoreo.gateway.openrouter import prefer_fast

        chain = prefer_fast(chain, ort.catalog_ttl_s)
    for raw in chain:
        entry = main_model if raw == "main" else expand_alias(settings, raw)
        if aux.free_only:
            entry = _free(entry)
        if aux.aux_probe_mode == "lazy" and _recently_failed(entry, aux.probe_ttl_s):
            continue
        try:
            if _usable(settings, entry, gateway, vision):
                return entry
        except Exception:  # never raise from routing
            continue
    if vision and "vision" in settings.models:
        return "vision"
    if "small" in settings.models:
        return "small"
    return main_model


__all__ = ["AUX_TASKS", "BACKGROUND_PURPOSES", "clear_failures", "note_failure", "resolve_aux_model"]
