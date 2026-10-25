"""LiteLLM Router gateway — the ONLY module that imports `litellm` (spec §10.1).

Routing groups from `settings.models` become Router `model_list` entries (one deployment
per model in the group). The Router does fallbacks; this module owns retry/backoff for
retryable errors that happen before the first streamed token (`num_retries=0` in the SDK).
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Callable
from typing import Any

import litellm
from litellm import exceptions as lx

from auoreo.core.settings import Settings
from auoreo.gateway.errors import ErrorCode, GatewayError
from auoreo.gateway.types import (
    Finish,
    LLMRequest,
    StreamItem,
    TextDelta,
    ToolCall,
    Usage,
    prompt_chars,
    shrink_data_urls,
)

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


# LiteLLM's `reasoning_effort` takes low | medium | high (mapped per provider, e.g. to Anthropic
# thinking budgets); our `max` has no portable equivalent, so it is sent as `high`.
_EFFORT = {"max": "high"}


def effort_param(effort: str) -> str:
    return _EFFORT.get(effort, effort)


def map_error(exc: BaseException) -> GatewayError:
    if isinstance(exc, GatewayError):
        return exc
    status = getattr(exc, "status_code", None)
    if status == 402:
        code: ErrorCode = "QUOTA"
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
        str(getattr(exc, "message", exc))[:2000],
        provider=getattr(exc, "llm_provider", None),
        status=status,
        retry_after_ms=retry_after,
    )


# Universal providers: a param a model does not accept (e.g. temperature on fixed-temperature
# reasoning models) is dropped by LiteLLM instead of failing the turn.
litellm.drop_params = True

# LiteLLM env-var name → completion kwarg. Names come from `litellm.validate_environment`,
# so any provider LiteLLM knows works by prefix: `anthropic/…` → ANTHROPIC_API_KEY,
# `gemini/…` → GEMINI_API_KEY | GOOGLE_API_KEY, `azure/…` → AZURE_API_KEY + _BASE + _VERSION.
_SUFFIX_PARAM = {"_API_KEY": "api_key", "_API_BASE": "api_base", "_API_VERSION": "api_version"}
_EXACT_PARAM = {"VERTEXAI_PROJECT": "vertex_project", "VERTEXAI_LOCATION": "vertex_location"}


def provider_of(model: str) -> str:
    """`openrouter/anthropic/claude-sonnet-4.5` → `openrouter` (the LiteLLM provider prefix)."""
    return model.split("/", 1)[0] if "/" in model else ""


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
    model_list: list[dict[str, Any]] = []
    for group, cfg in settings.models.items():
        for order, model in enumerate(cfg.models, start=1):
            params: dict[str, Any] = {**credential_params(model, secret)}
            params.update(model=model, order=order)
            if cfg.api_base:
                params["api_base"] = cfg.api_base
            entry: dict[str, Any] = {"model_name": group, "litellm_params": params}
            if cfg.max_input_tokens:
                entry["model_info"] = {"max_input_tokens": cfg.max_input_tokens}
            model_list.append(entry)
    return model_list


class LiteLLMGateway:
    name = "litellm"

    def __init__(self, settings: Settings, secret: Callable[[str], str | None]) -> None:
        self.settings = settings
        self.secret = secret
        self._model_list = build_model_list(settings, secret)
        self._router = self._make_router()

    def _make_router(self) -> Any:
        groups = {e["model_name"] for e in self._model_list}
        fallbacks = [{g: fb} for g, fb in self.settings.fallbacks.items() if g in groups]
        return litellm.Router(  # type: ignore[attr-defined]
            model_list=self._model_list,
            routing_strategy="simple-shuffle",
            fallbacks=fallbacks,
            context_window_fallbacks=fallbacks,
            enable_pre_call_checks=True,
            num_retries=0,
            allowed_fails=3,
            cooldown_time=5,
            max_fallbacks=5,
        )

    def _ensure(self, model: str) -> None:
        """Raw provider ids (not a group) get their own one-deployment group."""
        if any(e["model_name"] == model for e in self._model_list):
            return
        params: dict[str, Any] = {**credential_params(model, self.secret), "model": model}
        self._model_list.append({"model_name": model, "litellm_params": params})
        self._router = self._make_router()

    async def stream(self, request: LLMRequest) -> AsyncIterator[StreamItem]:
        self._ensure(request.model)
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": list(request.messages),
            "stream": True,
            "stream_options": {"include_usage": True},
            "num_retries": 0,
            "metadata": request.metadata,
        }
        if request.tools:
            kwargs["tools"] = list(request.tools)
            kwargs["tool_choice"] = request.tool_choice
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_tokens:
            kwargs["max_tokens"] = request.max_tokens
        if request.reasoning_effort:  # only when set; drop_params strips it for models without it
            kwargs["reasoning_effort"] = effort_param(request.reasoning_effort)
        for f in request.omit_fields:
            kwargs.pop(f, None)

        resp = await self._open(kwargs)
        calls: dict[int, dict[str, str]] = {}
        seen_usage: Usage | None = None
        text_len = 0
        finish = "stop"
        got_any = False
        try:
            async for chunk in resp:
                usage = getattr(chunk, "usage", None)
                if usage and (usage.prompt_tokens or usage.completion_tokens):
                    seen_usage = Usage(int(usage.prompt_tokens or 0), int(usage.completion_tokens or 0))
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta is not None and delta.content:
                    got_any = True
                    text_len += len(delta.content)
                    yield TextDelta(delta.content)
                for tc in getattr(delta, "tool_calls", None) or []:
                    got_any = True
                    slot = calls.setdefault(tc.index or 0, {"id": "", "name": "", "args": ""})
                    slot["id"] = tc.id or slot["id"]
                    if tc.function is not None:
                        slot["name"] += tc.function.name or ""
                        slot["args"] += tc.function.arguments or ""
                if choice.finish_reason:
                    finish = choice.finish_reason
        except Exception as exc:  # mid-stream failures surface as typed errors
            raise map_error(exc) from exc
        if not got_any:
            raise GatewayError("EMPTY_RESPONSE", "model returned no content")
        for idx in sorted(calls):
            c = calls[idx]
            yield ToolCall(id=c["id"] or f"call_{idx}", name=c["name"], arguments=c["args"] or "{}")
        if seen_usage is None:  # stream ended without usage: estimate, and say so (§10.8)
            prompt = prompt_chars(request.messages)
            seen_usage = Usage(prompt // 4, max(1, text_len // 4), estimated=True)
        yield seen_usage
        yield Finish(finish)

    async def _open(self, kwargs: dict[str, Any]) -> Any:
        delay = INITIAL_DELAY_MS
        for attempt in range(MAX_RETRIES + 1):
            try:
                return await self._router.acompletion(**kwargs)
            except Exception as exc:
                err = map_error(exc)
                if not err.retryable or attempt == MAX_RETRIES:
                    raise err from exc
                wait = min(err.retry_after_ms or delay, MAX_DELAY_MS)
                await asyncio.sleep(wait * (1 + random.uniform(-0.1, 0.1)) / 1000)
                delay = min(delay * 2, MAX_DELAY_MS)
        raise GatewayError("SERVER", "unreachable")

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
        model = self.settings.aliases.get(model, model)
        group = self.settings.models.get(model)
        return list(group.models) if group and group.models else [model]

    def context_window(self, model: str) -> int:
        group = self.settings.models.get(self.settings.aliases.get(model, model))
        if group is not None and group.max_input_tokens:
            return group.max_input_tokens
        deployments = self._deployments(model)
        if any("[1m]" in m for m in [model, *deployments]):
            return ONE_MILLION
        for dep in deployments:
            for candidate in _lookup_names(dep):
                try:
                    info = litellm.get_model_info(candidate)
                except Exception:
                    continue
                window = info.get("max_input_tokens") or info.get("max_tokens")
                if window:
                    return int(window)
        return DEFAULT_CONTEXT_WINDOW

    def price(self, model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
        for dep in self._deployments(model)[:1]:
            for candidate in _lookup_names(dep):
                try:
                    p, c = litellm.cost_per_token(
                        model=candidate, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
                    )
                except Exception:
                    continue
                if p or c:
                    return float(p + c)
        return None

    def supports_vision(self, model: str) -> bool:
        """True only when every deployment of the group accepts images: a fallback to a
        text-only deployment must not receive `image_url` parts."""
        return all(self._deployment_vision(dep) for dep in self._deployments(model))

    @staticmethod
    def _deployment_vision(model: str) -> bool:
        for candidate in _lookup_names(model):
            try:
                if litellm.supports_vision(model=candidate):
                    return True
            except Exception:
                continue
        return False


DEFAULT_CONTEXT_WINDOW = 128_000
ONE_MILLION = 1_000_000


def _lookup_names(model: str) -> list[str]:
    """`openrouter/anthropic/x` may be unmapped while `anthropic/x` is priced."""
    names = [model]
    if model.startswith("openrouter/") and model.count("/") >= 2:
        names.append(model.split("/", 1)[1])
    return names


__all__ = [
    "LiteLLMGateway",
    "build_model_list",
    "credential_params",
    "credential_status",
    "effort_param",
    "map_error",
    "provider_of",
]
