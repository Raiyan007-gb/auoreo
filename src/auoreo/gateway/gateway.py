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
from auoreo.gateway.types import Finish, LLMRequest, StreamItem, TextDelta, ToolCall, Usage

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


def build_model_list(settings: Settings, secret: Callable[[str], str | None]) -> list[dict[str, Any]]:
    model_list: list[dict[str, Any]] = []
    for group, cfg in settings.models.items():
        for order, model in enumerate(cfg.models, start=1):
            params: dict[str, Any] = {"model": model, "order": order}
            if cfg.api_base:
                params["api_base"] = cfg.api_base
            if model.startswith("openrouter/") and (key := secret("OPENROUTER_API_KEY")):
                params["api_key"] = key
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
        params: dict[str, Any] = {"model": model}
        if model.startswith("openrouter/") and (key := self.secret("OPENROUTER_API_KEY")):
            params["api_key"] = key
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
            prompt = sum(len(str(m.get("content") or "")) for m in request.messages)
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
            resp = await litellm.aembedding(model=model, input=texts, dimensions=dims)
        except Exception as exc:
            raise map_error(exc) from exc
        return [list(map(float, d["embedding"])) for d in resp.data]

    def count_tokens(self, model: str, text: str) -> int:
        try:
            return int(litellm.token_counter(model=model, text=text))
        except Exception:
            return max(1, len(text) // 4)


__all__ = ["LiteLLMGateway", "build_model_list", "map_error"]
