"""LiteLLM Router gateway — the ONLY module that imports `litellm` (spec §10.1).

Routing groups from `settings.models` become Router `model_list` entries (one deployment
per model in the group). The Router does fallbacks; this module owns retry/backoff for
retryable errors that happen before the first streamed token (`num_retries=0` in the SDK),
the 529 streak → fallback rule and auth recovery. Request shaping lives in `pipeline.py`
(+ `shaper.py` tool schemas, `structured.py` response_format), the model list in
`deploy.py`, stream assembly in `streaming.py`, auth/key-pool recovery in `authflow.py`,
budgets in `budgets.py`, context-window tiers in `ctxprobe.py`, aux routing in `aux.py`.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import random
from collections.abc import AsyncIterator, Callable
from typing import Any

import litellm
from litellm import exceptions as lx

from auoreo.core.redaction import redact
from auoreo.core.settings import Settings
from auoreo.gateway import aux, deploy, openrouter
from auoreo.gateway.authflow import SLOT_KEY, AuthFlow
from auoreo.gateway.budgets import BudgetTracker
from auoreo.gateway.ctxprobe import Window, guard_min_context, resolve_window
from auoreo.gateway.errors import MAX_CAUSE_CHARS, ErrorCode, FallbackTriggeredError, GatewayError
from auoreo.gateway.pipeline import (
    ShapeContext,
    clamp_max_tokens,
    effort_param,
    literal_values,
    load_variants,
    lookup_names,
    match_variant,
    provider_headers,
    provider_of,
    raw_body,
    reasoning_kwargs,
    resolve_effort,
    shape,
)
from auoreo.gateway.resolve import expand_alias, primary_deployment
from auoreo.gateway.shaper import shape_for_model
from auoreo.gateway.streaming import assemble
from auoreo.gateway.structured import check_support, downgrade, normalize_response_format
from auoreo.gateway.types import LLMRequest, StreamItem, Usage, prompt_chars, shrink_data_urls

log = logging.getLogger(__name__)
litellm.suppress_debug_info = True

# Order matters: subclasses before their bases (e.g. ContextWindowExceeded ⊂ BadRequest).
_ERROR_MAP: tuple[tuple[type[BaseException], ErrorCode], ...] = (
    (lx.ContextWindowExceededError, "CONTEXT"),
    (lx.ContentPolicyViolationError, "CONTENT_POLICY"),
    (lx.BudgetExceededError, "BUDGET"),
    (lx.AuthenticationError, "AUTH"),
    (lx.PermissionDeniedError, "AUTH"),
    (lx.RateLimitError, "RATE_LIMIT"),
    (lx.NotFoundError, "MODEL_NOT_FOUND"),
    (lx.Timeout, "TIMEOUT"),
    (lx.APIConnectionError, "TRANSPORT"),
    (lx.UnsupportedParamsError, "INVALID_REQUEST"),
    (lx.BadRequestError, "INVALID_REQUEST"),
    (lx.InternalServerError, "SERVER"),
    (lx.ServiceUnavailableError, "SERVER"),
    (lx.APIError, "SERVER"),
)

MAX_RETRIES = 5
INITIAL_DELAY_MS = 500
MAX_DELAY_MS = 10_000
TURN_STATE_HEADER = "x-auoreo-turn-state"


async def _backoff_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def map_error(exc: BaseException) -> GatewayError:
    if isinstance(exc, GatewayError):
        return exc
    status = getattr(exc, "status_code", None)
    message = str(getattr(exc, "message", exc))
    cause = redact(raw_body(exc))[:MAX_CAUSE_CHARS]
    overloaded = status == 529 or "overloaded" in f"{message} {cause}".lower()
    if status == 402:
        code: ErrorCode = "QUOTA"
    elif overloaded:  # 529 → RATE_LIMIT (spec §24.4); the streak is tracked via `overloaded`
        code = "RATE_LIMIT"
    else:
        code = next((c for cls, c in _ERROR_MAP if isinstance(exc, cls)), "SERVER")
    retry_after = None
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    if "retry-after" in headers:
        try:
            retry_after = int(float(headers["retry-after"]) * 1000)
        except ValueError:
            retry_after = None
    return GatewayError(
        code,
        redact(message)[:2000],
        provider=getattr(exc, "llm_provider", None),
        status=status,
        retry_after_ms=retry_after,
        request_id=headers.get("request-id") or headers.get("x-request-id"),
        cause=cause,
        overloaded=overloaded,
    )


# Universal providers: a param a model does not accept (e.g. temperature on fixed-temperature
# reasoning models) is dropped by LiteLLM instead of failing the turn.
litellm.drop_params = True

# LiteLLM env-var name → completion kwarg. Names come from `litellm.validate_environment`,
# so any provider LiteLLM knows works by prefix: `anthropic/…` → ANTHROPIC_API_KEY,
# `gemini/…` → GEMINI_API_KEY | GOOGLE_API_KEY, `azure/…` → AZURE_API_KEY + _BASE + _VERSION.
_SUFFIX_PARAM = {"_API_KEY": "api_key", "_API_BASE": "api_base", "_API_VERSION": "api_version"}
_EXACT_PARAM = {"VERTEXAI_PROJECT": "vertex_project", "VERTEXAI_LOCATION": "vertex_location"}


def env_names(model: str) -> list[str]:
    """Credential env names LiteLLM still needs for `model` (empty when the env has them)."""
    try:
        found = litellm.validate_environment(model)
    except Exception:  # unknown provider/model: fall back to the naming convention
        return []
    return [str(n) for n in found.get("missing_keys") or []]


def _param_for(name: str) -> str | None:
    if name in _EXACT_PARAM:
        return _EXACT_PARAM[name]
    return next((p for sfx, p in _SUFFIX_PARAM.items() if name.endswith(sfx)), None)


def credential_params(model: str, secret: Callable[[str], str | None]) -> dict[str, str]:
    """Credentials for `model` from env / keyring / secrets.db, keyed by LiteLLM kwarg.

    The provider prefix picks the names; `<PREFIX>_API_KEY` is the fallback convention for
    providers LiteLLM cannot describe. Keys only in the secret store (not in the env) are
    passed explicitly, so LiteLLM never needs them in `os.environ`."""
    params: dict[str, str] = {}
    for name in env_names(model):
        param = _param_for(name)
        if param and param not in params and (value := secret(name)):
            params[param] = value
    prefix = provider_of(model).upper().replace("-", "_")
    if prefix and "api_key" not in params and (value := secret(f"{prefix}_API_KEY")):
        params["api_key"] = value
    return params


def credential_status(model: str, secret: Callable[[str], str | None]) -> tuple[bool, str]:
    """`(ready, detail)` for doctor: which env names are present or missing for `model`."""
    names = env_names(model)
    keys = [n for n in names if n.endswith("_API_KEY")]
    missing = [n for n in names if n not in keys and not secret(n)]
    if keys and not any(secret(k) for k in keys):
        missing.insert(0, " or ".join(keys))
    if missing:
        return False, f"{model}: missing {', '.join(missing)}"
    convention = f"{provider_of(model).upper().replace('-', '_')}_API_KEY"
    have = [n for n in names if secret(n)] or ([convention] if secret(convention) else [])
    return True, f"{model}: {', '.join(have) or 'credentials found in environment'}"


def build_model_list(settings: Settings, secret: Callable[[str], str | None]) -> list[dict[str, Any]]:
    return deploy.build_model_list(settings, lambda m: credential_params(m, secret))


@functools.cache
def supported_pre_call_checks() -> frozenset[str]:
    """`optional_pre_call_checks` values the installed Router accepts (introspected)."""
    router_cls = litellm.Router  # type: ignore[attr-defined]
    param = inspect.signature(router_cls.__init__).parameters.get("optional_pre_call_checks")
    return frozenset(literal_values(param.annotation) if param is not None else ())


class LiteLLMGateway:
    name = "litellm"

    def __init__(self, settings: Settings, secret: Callable[[str], str | None]) -> None:
        self.settings = settings
        self.secret = secret
        self.variants: list[Any] | None = None  # lazily loaded prompts/variants.yaml
        self.auth = AuthFlow(self)  # --- I2: key pools, #prev grace fallback, hot reload
        self.budgets = BudgetTracker(settings.i2.budgets)
        self._model_list = build_model_list(settings, secret)
        self._router = self._make_router()

    def _make_router(self) -> Any:
        kw = deploy.router_kwargs(self.settings, self._model_list, supported_pre_call_checks())
        if self.settings.gateway_tuning.session_affinity and "optional_pre_call_checks" not in kw:
            log.info("litellm Router has no session_affinity pre-call check; skipping")
        return litellm.Router(**kw)  # type: ignore[attr-defined]

    def _creds(self, model: str) -> dict[str, str]:
        return credential_params(model, self.secret)

    def _ensure(self, model: str) -> None:
        """Raw provider ids (not a group) get their own one-deployment group."""
        if any(e["model_name"] == model for e in self._model_list):
            return
        self._model_list.append(deploy.deployment_entry(self.settings, model, model, self._creds))
        self._router = self._make_router()

    def reload_credentials(self) -> None:
        """Hot reload after a secrets write/rotate: rebuild every deployment's credentials and
        the Router (its client/key cache goes with it) without a restart (spec §25.14)."""
        raw = [e["model_name"] for e in self._model_list if e["model_name"] not in self.settings.models]
        self._model_list = build_model_list(self.settings, self.secret)
        for model in dict.fromkeys(raw):
            self._model_list.append(deploy.deployment_entry(self.settings, model, model, self._creds))
        self.auth.reload_pools()
        self._router = self._make_router()

    def has_credentials(self, model: str) -> bool:
        """Aux-chain probe (no client construction): are `model`'s credentials present?"""
        return self.auth.pooled(model) or credential_status(model, self.secret)[0]

    def _variant_for(self, deployment: str) -> Any:
        if self.variants is None:
            self.variants = load_variants(self.settings)
        return match_variant(self.variants, deployment)

    def _kwargs(self, request: LLMRequest, model: str) -> dict[str, Any]:
        """LiteLLM kwargs for one attempt on `model`, shaped by the request pipeline."""
        tuning, i2 = self.settings.gateway_tuning, self.settings.i2
        deployment = primary_deployment(self.settings, model)
        variant = request.variant or self._variant_for(deployment)
        metadata = dict(request.metadata)
        messages = list(request.messages)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "num_retries": 0,
            "metadata": metadata,
        }
        if request.tools:
            shaping = i2.tool_shaping
            kwargs["tools"] = shape_for_model(
                deployment, request.tools, strict=shaping.strict, max_enum=shaping.max_enum
            )
            kwargs["tool_choice"] = request.tool_choice
        fmt = normalize_response_format(request.response_format)
        if fmt is not None:
            deps = [deploy.provider_id(self.settings, d) for d in self._deployments(model)]
            fallback = i2.fallback_structured
            if not check_support(fmt, deps, self.supports_response_schema, fallback_structured=fallback):
                fmt, kwargs["messages"] = downgrade(fmt, messages)
            kwargs["response_format"] = fmt
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_tokens:
            prompt = prompt_chars(request.messages) // 4
            window = self.context_window(model)
            floor, buffer = tuning.max_tokens_floor, tuning.overflow_buffer
            kwargs["max_tokens"] = clamp_max_tokens(request.max_tokens, window, prompt, floor, buffer)
        effort = resolve_effort(request.reasoning_effort, request.session_effort)
        kwargs.update(reasoning_kwargs(effort, deployment, variant))
        if turn := request.turn_state or metadata.get("run_id"):
            metadata[TURN_STATE_HEADER] = str(turn)
            kwargs["extra_headers"] = {
                **provider_headers(self.settings, deployment),
                **deploy.split_1m(deployment)[1],
                TURN_STATE_HEADER: str(turn),
            }
        if all(openrouter.is_openrouter(d) for d in self._deployments(model)):
            prefs = openrouter.provider_prefs(i2.openrouter, structured=bool(request.tools or fmt))
            kwargs["extra_body"] = {"usage": {"include": True}, **({"provider": prefs} if prefs else {})}
        self.auth.apply_pool(kwargs, self._deployments(model))
        ctx = ShapeContext(
            model=deployment,
            variant=variant,
            vision=self.supports_vision(model),
            caching=request.prompt_caching and tuning.prompt_caching,
            cache_ttl=tuning.cache_ttl,
            omit=request.omit_fields,
        )
        return shape(kwargs, ctx)

    def _pick(self, model: str, tags: list[str]) -> str | None:
        """Budget pre-pick: `model` when no deployment is over budget, else the first
        deployment still under every ceiling (called directly), None when all are spent."""
        if not self.budgets.active:
            return model
        deps = self._deployments(model)
        ok = [d for d in deps if self.budgets.exhausted(deploy.provider_id(self.settings, d), tags) is None]
        if len(ok) == len(deps):
            return model
        if ok:
            self._ensure(ok[0])
        return ok[0] if ok else None

    async def stream(self, request: LLMRequest) -> AsyncIterator[StreamItem]:
        self.auth.maybe_reload()
        self._ensure(request.model)
        background = request.metadata.get("purpose") in aux.BACKGROUND_PURPOSES
        if not background:
            guard_min_context(request.model, self._window(request.model), self.settings.i2.min_context)
        chain = (
            [request.model]
            if background
            else [request.model, *self.settings.fallbacks.get(request.model, [])]
        )
        tags = [str(t) for t in request.metadata.get("tags") or []]
        targets = [(m, t) for m in chain if (t := self._pick(m, tags)) is not None]
        if not targets:
            raise GatewayError(
                "BUDGET", f"every deployment of {request.model} is over its daily budget", status=429
            )
        resp: Any = None
        served, kwargs = request.model, {}
        for i, (model, target) in enumerate(targets):  # turn-scoped: primary retried next turn
            self._ensure(target)
            try:
                kwargs = self._kwargs(request, target)
                resp = await self._open(kwargs)
                served = target
                break
            except FallbackTriggeredError:
                if i == len(targets) - 1:
                    raise
                log.warning("%s overloaded; falling back to %s for this turn", model, targets[i + 1][0])
        deps = [deploy.provider_id(self.settings, d) for d in self._deployments(served)]
        idle = self.settings.gateway_tuning.stream_idle_timeout_s
        async for item in assemble(resp, request, idle_s=idle, deployments=deps, map_error=map_error):
            if isinstance(item, Usage):
                self._spend(item, served, kwargs)
            yield item

    def _spend(self, usage: Usage, model: str, kwargs: dict[str, Any]) -> None:
        dep = usage.deployment or deploy.provider_id(self.settings, primary_deployment(self.settings, model))
        cost = usage.cost_usd
        if cost is None:
            cost = self.price(dep, usage.prompt_tokens, usage.completion_tokens)
        slot = (kwargs.get("metadata") or {}).get(SLOT_KEY)
        self.budgets.add(dep, cost, usage.tags, slot)
        if slot:
            self.auth.succeeded(slot)

    async def _open(self, kwargs: dict[str, Any]) -> Any:
        """Open the stream. Foreground: backoff on retryable codes, N consecutive overloaded
        errors → `FallbackTriggeredError`. Background purposes fail fast (no retry). Auth /
        rate errors go through `AuthFlow` first (pool rotation, store re-read, `#prev`)."""
        model = str(kwargs["model"])
        background = (kwargs.get("metadata") or {}).get("purpose") in aux.BACKGROUND_PURPOSES
        limit = self.settings.gateway_tuning.overload_fallback_after
        delay, streak = INITIAL_DELAY_MS, 0
        recovery = self.auth.attempt()
        for attempt in range(MAX_RETRIES + 1):
            try:
                return await self._router.acompletion(**kwargs)
            except Exception as exc:
                err = map_error(exc)
                if recovery.recover(err, model, kwargs):
                    continue
                if background:
                    aux.note_failure(model)
                    raise err from exc
                streak = streak + 1 if err.overloaded else 0
                if streak >= limit:
                    raise FallbackTriggeredError(model, streak, err) from exc
                if not err.retryable or attempt == MAX_RETRIES:
                    raise err from exc
                wait = min(err.retry_after_ms or delay, MAX_DELAY_MS)
                await _backoff_sleep(wait * (1 + random.uniform(-0.1, 0.1)) / 1000)
                delay = min(delay * 2, MAX_DELAY_MS)
        raise GatewayError("SERVER", "unreachable")

    def key_names(self, model: str) -> list[str]:
        """`*_API_KEY` secret names `model`'s deployments authenticate with (for `#prev`)."""
        names: list[str] = []
        for dep in self._deployments(model):
            real = deploy.provider_id(self.settings, dep)
            names += [n for n in env_names(real) if n.endswith("_API_KEY")]
            names.append(f"{provider_of(real).upper().replace('-', '_')}_API_KEY")
        return list(dict.fromkeys(n for n in names if n != "_API_KEY"))

    async def embed(self, model: str, texts: list[str], dims: int) -> list[list[float]]:
        try:
            creds = credential_params(model, self.secret)
            resp = await litellm.aembedding(model=model, input=texts, dimensions=dims, **creds)
        except Exception as exc:
            raise map_error(exc) from exc
        return [list(map(float, d["embedding"])) for d in resp.data]

    def count_tokens(self, model: str, text: str) -> int:
        try:
            return int(litellm.token_counter(model=model, text=shrink_data_urls(text)))
        except Exception:
            return max(1, len(shrink_data_urls(text)) // 4)

    def _deployments(self, model: str) -> list[str]:
        """A routing group expands to its deployments; a raw id is its own deployment."""
        model = expand_alias(self.settings, model)
        group = self.settings.models.get(model)
        return list(group.models) if group and group.models else [model]

    def _window(self, model: str) -> Window:
        group = self.settings.models.get(expand_alias(self.settings, model))
        tuned = self.settings.i2.deployments
        return resolve_window(
            model,
            self._deployments(model),
            pinned=group.max_input_tokens if group is not None else None,
            per_deployment=lambda d: t.max_input_tokens if (t := tuned.get(d)) else None,
            info=self._info_window,
            tiers=self.settings.i2.probe_tiers,
            default=self.settings.i2.probe_default,
        )

    def context_window(self, model: str) -> int:
        return self._window(model).tokens

    def _info_window(self, dep: str) -> int | None:
        tuned = self.settings.i2.deployments.get(dep)
        names = [tuned.base_model] if tuned and tuned.base_model else []
        for candidate in [*names, *lookup_names(deploy.provider_id(self.settings, dep))]:
            try:
                info = litellm.get_model_info(candidate)
            except Exception:
                continue
            if window := info.get("max_input_tokens") or info.get("max_tokens"):
                return int(window)
        return None

    def price(self, model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
        for dep in self._deployments(model)[:1]:
            for candidate in lookup_names(deploy.provider_id(self.settings, dep)):
                try:
                    p, c = litellm.cost_per_token(
                        model=candidate, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
                    )
                except Exception:
                    continue
                if p or c:
                    return float(p + c)
        return None

    def supports_response_schema(self, model: str) -> bool:
        return _any_candidate(model, lambda c: bool(litellm.supports_response_schema(model=c)))

    def supports_vision(self, model: str) -> bool:
        """True only when every deployment of the group accepts images: a fallback to a
        text-only deployment must not receive `image_url` parts."""
        return all(
            _any_candidate(
                deploy.provider_id(self.settings, d), lambda c: bool(litellm.supports_vision(model=c))
            )
            for d in self._deployments(model)
        )


def _any_candidate(model: str, test: Callable[[str], bool]) -> bool:
    for candidate in lookup_names(model):
        try:
            if test(candidate):
                return True
        except Exception:
            continue
    return False


__all__ = [
    "LiteLLMGateway",
    "build_model_list",
    "credential_params",
    "credential_status",
    "effort_param",
    "map_error",
    "provider_headers",
    "provider_of",
    "supported_pre_call_checks",
]
