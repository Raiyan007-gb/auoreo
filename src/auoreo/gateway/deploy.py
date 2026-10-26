"""Router `model_list` + Router kwargs from settings (spec §10.2, §10.3, §25.9). No `litellm`.

Credentials come through an injected `creds(model) → {api_key, …}` (the gateway module owns
the LiteLLM env-name lookup). Per deployment:

- `order` tiers by position (override: `i2.deployments[id].order`), `weight` from
  `gateway_tuning.weights`, `max_parallel_requests` in `litellm_params`;
- `model_info` carries `max_input_tokens`/`max_output_tokens`/`base_model`/`allowed_fails`/
  `cooldown_time` (router-internal; never leaked into `litellm_params`, spec §25.9 L1000);
- `[1m]` ids (`anthropic/claude-opus-5-5[1m]`) strip the marker and add the provider's 1M
  beta header (`anthropic-beta: context-1m-2025-08-07`) with a 1M `max_input_tokens`;
- OpenRouter ids get the static `provider` preferences in `extra_body` and `:nitro` gating.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from auoreo.gateway import openrouter
from auoreo.gateway.ctxprobe import ONE_MILLION
from auoreo.gateway.pipeline import provider_headers, provider_of

if TYPE_CHECKING:
    from auoreo.core.settings import Settings

ONE_M = "[1m]"
ANTHROPIC_1M_BETA = "context-1m-2025-08-07"
"""Anthropic's 1M-context beta (`anthropic-beta` header; platform.claude.com beta-headers)."""
_ONE_M_HEADERS: dict[str, dict[str, str]] = {"anthropic": {"anthropic-beta": ANTHROPIC_1M_BETA}}
RESERVED_GROUP_PREFIX = "auoreo-"
Creds = Callable[[str], dict[str, str]]


def split_1m(model: str) -> tuple[str, dict[str, str]]:
    """`anthropic/claude-opus-5-5[1m]` → (`anthropic/claude-opus-5-5`, {anthropic-beta: …})."""
    if not model.endswith(ONE_M):
        return model, {}
    base = model.removesuffix(ONE_M)
    return base, dict(_ONE_M_HEADERS.get(provider_of(base), {}))


def provider_id(settings: Settings, model: str) -> str:
    """The id LiteLLM is called with: `[1m]` stripped, `:nitro` gated."""
    base, _ = split_1m(model)
    if openrouter.is_openrouter(base):
        base = openrouter.strip_nitro(base, settings.i2.openrouter.allow_nitro)
    return base


def deployment_entry(
    settings: Settings, group: str, model: str, creds: Creds, *, order: int | None = None
) -> dict[str, Any]:
    """One `model_list` entry for deployment `model` serving `group`."""
    tuning = settings.i2.deployments.get(model)
    real, beta = split_1m(model)
    real = provider_id(settings, model)
    params: dict[str, Any] = {**creds(real), "model": real}
    if order is not None:
        params["order"] = tuning.order if tuning and tuning.order else order
    headers = {**provider_headers(settings, real), **beta}
    if headers:
        params["extra_headers"] = headers
    if openrouter.is_openrouter(real) and (
        prefs := openrouter.provider_prefs(settings.i2.openrouter, structured=False)
    ):
        params["extra_body"] = {"provider": prefs}
    info: dict[str, Any] = {}
    if beta or model.endswith(ONE_M):
        info["max_input_tokens"] = ONE_MILLION
    if tuning is not None:
        if tuning.max_parallel_requests:
            params["max_parallel_requests"] = tuning.max_parallel_requests
        extra = {
            "max_input_tokens": tuning.max_input_tokens,
            "max_output_tokens": tuning.max_output_tokens,
            "base_model": tuning.base_model,
            "allowed_fails": tuning.allowed_fails,
            "cooldown_time": tuning.cooldown,
        }
        info.update({k: v for k, v in extra.items() if v is not None})
    entry: dict[str, Any] = {"model_name": group, "litellm_params": params}
    if info:
        entry["model_info"] = info
    return entry


def build_model_list(settings: Settings, creds: Creds) -> list[dict[str, Any]]:
    model_list: list[dict[str, Any]] = []
    for group, cfg in settings.models.items():
        weights = settings.gateway_tuning.weights.get(group, [])
        for order, model in enumerate(cfg.models, start=1):
            entry = deployment_entry(settings, group, model, creds, order=order)
            params = entry["litellm_params"]
            if cfg.api_base:
                params["api_base"] = cfg.api_base
            if order <= len(weights):
                params["weight"] = weights[order - 1]
            if cfg.max_input_tokens:
                entry.setdefault("model_info", {})["max_input_tokens"] = cfg.max_input_tokens
            model_list.append(entry)
    return model_list


def routing_groups(settings: Settings, groups: set[str]) -> list[dict[str, Any]]:
    """`i2.group_strategy` → LiteLLM `routing_groups` (names reserved as `auoreo-<group>`)."""
    return [
        {"group_name": f"{RESERVED_GROUP_PREFIX}{g}", "models": [g], "routing_strategy": strategy}
        for g, strategy in sorted(settings.i2.group_strategy.items())
        if g in groups
    ]


def router_kwargs(
    settings: Settings, model_list: list[dict[str, Any]], pre_call_checks: frozenset[str]
) -> dict[str, Any]:
    tuning = settings.gateway_tuning
    groups = {e["model_name"] for e in model_list}
    fallbacks = [{g: fb} for g, fb in settings.fallbacks.items() if g in groups]
    policy = [{g: fb} for g, fb in tuning.content_policy_fallbacks.items() if g in groups]
    kw: dict[str, Any] = {
        "model_list": model_list,
        "routing_strategy": "simple-shuffle",
        "fallbacks": fallbacks,
        "context_window_fallbacks": fallbacks,
        "content_policy_fallbacks": policy,
        "enable_pre_call_checks": True,
        "num_retries": 0,
        "allowed_fails": 3,
        "cooldown_time": 5,
        "max_fallbacks": 5,
    }
    if any(tuning.weights.values()):
        kw["enable_weighted_failover"] = True
    if tuning.session_affinity and "session_affinity" in pre_call_checks:
        kw["optional_pre_call_checks"] = ["session_affinity"]
    if rg := routing_groups(settings, groups):
        kw["routing_groups"] = rg
    return kw


__all__ = [
    "ANTHROPIC_1M_BETA",
    "build_model_list",
    "deployment_entry",
    "provider_id",
    "router_kwargs",
    "routing_groups",
    "split_1m",
]
