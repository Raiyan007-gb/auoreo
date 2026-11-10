"""Request shaping pipeline (spec §10.6, §12 cache breakpoints). No `litellm` here.

`shape()` runs a fixed, dict-dispatched sequence over the LiteLLM kwargs:
`unsupported_parts → normalize → apply_caching → remap`. Effort resolution and the
`reasoning_vocab` clamp live here too, as does the near-limit `max_tokens` computation.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import re
import typing
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from auoreo.gateway.errors import GatewayError

if TYPE_CHECKING:
    from auoreo.core.settings import Settings
    from auoreo.harness.manifests import Variant

log = logging.getLogger(__name__)

EFFORTS: tuple[str, ...] = ("low", "medium", "high", "max")
IMAGE_PLACEHOLDER = "[image omitted: this model does not accept images]"
MAX_BREAKPOINTS = 4  # Anthropic's per-request cache_control limit

# First match wins; the family picks the caching strategy and default reasoning knobs.
_FAMILIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anthropic", re.compile(r"claude|(^|/)anthropic[/.]", re.I)),
    ("gemini", re.compile(r"gemini", re.I)),
    ("kimi", re.compile(r"kimi|moonshot", re.I)),
    ("openai", re.compile(r"(^|/)(gpt-|o[0-9]|openai/)", re.I)),
)
_OPUS_CLASS = re.compile(r"opus|fable|mythos", re.I)


def family_of(model: str) -> str:
    return next((name for name, rx in _FAMILIES if rx.search(model)), "default")


def is_opus_class(model: str) -> bool:
    """Models that accept `max` effort natively (no `max→high` clamp)."""
    return bool(_OPUS_CLASS.search(model))


# ------------------------------------------------------------------ effort
def resolve_effort(request_effort: str | None, session_effort: str | None = None) -> str | None:
    """`AUOREO_EFFORT` env → session override → agent manifest / policy choice."""
    env = os.environ.get("AUOREO_EFFORT", "").strip().lower()
    for candidate in (env, session_effort, request_effort):
        if candidate in EFFORTS:
            return candidate
    return None


def default_vocab(model: str) -> dict[str, str]:
    return {
        "low": "low",
        "medium": "medium",
        "high": "high",
        "max": "max" if is_opus_class(model) else "high",
    }


def map_effort(effort: str, model: str, vocab: dict[str, str] | None) -> str | None:
    """Clamp through the vocab: the level itself, else the nearest weaker level listed."""
    table = vocab if vocab is not None else default_vocab(model)
    if effort not in EFFORTS:
        return None
    for level in reversed(EFFORTS[: EFFORTS.index(effort) + 1]):
        if level in table:
            return table[level]
    return None


_DISPLAY = {"hidden": "omitted", "summary": "summarized", "full": "summarized"}


def reasoning_kwargs(effort: str | None, model: str, variant: Variant | None) -> dict[str, Any]:
    """Tri-state: `off` → nothing; `on` → always (default medium); `auto` → only when set."""
    mode = variant.reasoning if variant is not None else "auto"
    if mode == "off" or (mode == "auto" and effort is None):
        return {}
    value = map_effort(effort or "medium", model, variant.reasoning_vocab if variant else None)
    if value is None:
        return {}
    out: dict[str, Any] = {"reasoning_effort": value}
    display = variant.thinking_display if variant is not None else None
    if display and family_of(model) == "anthropic":
        out["thinking"] = {"type": "adaptive", "display": _DISPLAY[display]}
    return out


def clamp_max_tokens(
    requested: int | None, window: int, prompt_tokens: int, floor: int, buffer: int
) -> int | None:
    """Shrink `max_tokens` to fit `window` (keeping `buffer` spare), never below `floor`."""
    if not requested:
        return requested
    available = window - prompt_tokens - buffer
    if requested <= available:
        return requested
    return min(requested, max(floor, available))


# ------------------------------------------------------------------ provider helpers
def effort_param(effort: str, model: str = "") -> str:
    """AUOREO effort → LiteLLM `reasoning_effort` with the default vocab (`max` only on
    Opus-class models; elsewhere it clamps to `high`)."""
    return map_effort(effort, model, None) or effort


def raw_body(exc: BaseException) -> str:
    body = getattr(exc, "body", None)
    if not body:
        try:
            body = getattr(getattr(exc, "response", None), "text", None)
        except Exception:  # a streamed response may have no readable body
            body = None
    return str(body or exc)


def provider_of(model: str) -> str:
    """`openrouter/anthropic/claude-sonnet-4.5` → `openrouter` (the LiteLLM provider prefix)."""
    return model.split("/", 1)[0] if "/" in model else ""


def provider_headers(settings: Settings, model: str) -> dict[str, str]:
    """Attribution headers OpenRouter asks apps to send (spec §10.8)."""
    if provider_of(model) != "openrouter":
        return {}
    t = settings.gateway_tuning
    return {"HTTP-Referer": t.openrouter_referer, "X-Title": t.openrouter_title}


def literal_values(annotation: Any) -> set[str]:
    if isinstance(annotation, str):  # postponed annotation: best-effort text match
        return {"session_affinity"} if "session_affinity" in annotation else set()
    out = {a for a in typing.get_args(annotation) if isinstance(a, str)}
    for arg in typing.get_args(annotation):
        out |= literal_values(arg)
    return out


def lookup_names(model: str) -> list[str]:
    """`openrouter/anthropic/x` may be unmapped while `anthropic/x` is priced."""
    names = [model]
    if model.startswith("openrouter/") and model.count("/") >= 2:
        names.append(model.split("/", 1)[1])
    return names


async def idle_chunks(stream: Any, idle_s: float) -> AsyncIterator[Any]:
    """Iterate a provider stream; no chunk for `idle_s` → `GatewayError("TRANSPORT")`."""
    chunks = stream.__aiter__()
    while True:
        try:
            chunk = await asyncio.wait_for(chunks.__anext__(), idle_s)
        except StopAsyncIteration:
            return
        except TimeoutError:
            raise GatewayError("TRANSPORT", f"stream idle for {idle_s:.0f}s") from None
        yield chunk


# ------------------------------------------------------------------ variants
def match_variant(variants: list[Variant], model: str) -> Variant | None:
    provider = model.split("/", 1)[0] if "/" in model else ""
    for v in variants:
        if v.provider in {"*", provider} and re.search(v.model_regex, model):
            return v
    return None


def load_variants(settings: Settings) -> list[Variant]:
    """`prompts/variants.yaml` from the highest-precedence definition root that has one."""
    import yaml

    from auoreo.core.paths import bundled_root
    from auoreo.harness.discovery import default_roots
    from auoreo.harness.manifests import VariantsFile

    for root in default_roots(settings.home, Path.cwd(), bundled_root(), settings.extra_roots):
        path = root.path / "prompts" / "variants.yaml"
        if not path.is_file():
            continue
        try:
            return list(VariantsFile.model_validate(yaml.safe_load(path.read_text()) or {}).variants)
        except Exception as exc:  # an invalid overlay must not break every call
            log.warning("ignoring %s: %s", path, exc)
    return []


# ------------------------------------------------------------------ pipeline
@dataclass(frozen=True)
class ShapeContext:
    model: str  # primary deployment id (provider form)
    variant: Variant | None = None
    vision: bool = True
    caching: bool = True
    cache_ttl: str = "5m"
    omit: tuple[str, ...] = ()
    cache_layout: str = "legacy"
    """`legacy`: breakpoints on tools, system, last user message. `tail` (M7): plus the last
    markable message, so a tool loop's growing tail is cached too."""
    cache_key: str = ""
    """M7 `openai_cache_key`: sent as `prompt_cache_key` to OpenAI-family models ("" = off)."""


def unsupported_parts(kwargs: dict[str, Any], ctx: ShapeContext) -> dict[str, Any]:
    if ctx.vision:
        return kwargs
    msgs = []
    for m in kwargs["messages"]:
        content = m.get("content")
        if isinstance(content, list) and any(_is_image(p) for p in content):
            parts = [{"type": "text", "text": IMAGE_PLACEHOLDER} if _is_image(p) else p for p in content]
            m = {**m, "content": parts}
        msgs.append(m)
    return {**kwargs, "messages": msgs}


def _is_image(part: Any) -> bool:
    return isinstance(part, dict) and part.get("type") == "image_url"


def normalize(kwargs: dict[str, Any], ctx: ShapeContext) -> dict[str, Any]:
    v = ctx.variant
    if v is None or (v.block_binding == "keep" and not v.interleaved_field):
        return kwargs
    msgs = []
    for m in kwargs["messages"]:
        if m.get("role") == "assistant":
            m = dict(m)
            if v.block_binding == "strip":
                m.pop("thinking_blocks", None)
                m.pop("reasoning_content", None)
            elif (
                v.interleaved_field
                and "reasoning_content" in m
                and v.interleaved_field != "reasoning_content"
            ):
                m[v.interleaved_field] = m.pop("reasoning_content")
        msgs.append(m)
    return {**kwargs, "messages": msgs}


def _mark_last_block(message: dict[str, Any], cc: dict[str, str]) -> dict[str, Any]:
    content = message.get("content")
    if isinstance(content, str):
        if not content:
            return message
        return {**message, "content": [{"type": "text", "text": content, "cache_control": cc}]}
    if isinstance(content, list) and content:
        parts = copy.copy(content)
        parts[-1] = {**parts[-1], "cache_control": cc}
        return {**message, "content": parts}
    return message


_UNMARKABLE_BLOCKS = frozenset({"thinking", "redacted_thinking"})


def _markable(message: dict[str, Any]) -> bool:
    """A message Anthropic accepts a breakpoint on: non-empty, not ending in a thinking block."""
    content = message.get("content")
    if not content:
        return False
    if isinstance(content, list):
        last = content[-1]
        return not (isinstance(last, dict) and last.get("type") in _UNMARKABLE_BLOCKS)
    return True


def _mark_tail(msgs: list[dict[str, Any]], cc: dict[str, str]) -> None:
    """Mark the last markable message in place (`role: tool` takes a message-level key,
    which LiteLLM copies onto the Anthropic `tool_result` block); no-op if already marked."""
    for i in range(len(msgs) - 1, 0, -1):
        if not _markable(msgs[i]):
            continue
        if "cache_control" not in str(msgs[i]):
            msgs[i] = (
                {**msgs[i], "cache_control": cc}
                if msgs[i].get("role") == "tool"
                else _mark_last_block(msgs[i], cc)
            )
        return


def _anthropic_cache(kwargs: dict[str, Any], ctx: ShapeContext) -> dict[str, Any]:
    """Breakpoints: last tool definition | stable system prompt | last user message
    (| last markable message with the `tail` layout)."""
    cc: dict[str, str] = {"type": "ephemeral"}
    if ctx.cache_ttl != "5m":
        cc["ttl"] = ctx.cache_ttl
    out = dict(kwargs)
    used = 0
    tools = list(out.get("tools") or [])
    if tools:
        tools[-1] = {**tools[-1], "cache_control": cc}
        out["tools"] = tools
        used += 1
    msgs = list(out["messages"])
    if msgs and msgs[0].get("role") == "system":
        msgs[0] = _mark_last_block(msgs[0], cc)
        used += 1
    last_user = next((i for i in range(len(msgs) - 1, 0, -1) if msgs[i].get("role") == "user"), None)
    if last_user is not None and used < MAX_BREAKPOINTS:
        msgs[last_user] = _mark_last_block(msgs[last_user], cc)
        used += 1
    if ctx.cache_layout == "tail" and used < MAX_BREAKPOINTS:
        _mark_tail(msgs, cc)
    out["messages"] = msgs
    return out


_CACHE_KEY_PROVIDERS = frozenset({"", "openai", "azure"})
"""LiteLLM providers that accept `prompt_cache_key` (elsewhere it would be dropped)."""


def _openai_cache(kwargs: dict[str, Any], ctx: ShapeContext) -> dict[str, Any]:
    """OpenAI caches prefixes on its own; a stable `prompt_cache_key` routes the requests of
    one session to the same cache. A caller-supplied key wins."""
    if not ctx.cache_key or "prompt_cache_key" in kwargs:
        return kwargs
    if provider_of(ctx.model) not in _CACHE_KEY_PROVIDERS:
        return kwargs
    return {**kwargs, "prompt_cache_key": ctx.cache_key}


_CACHERS: dict[str, Callable[[dict[str, Any], ShapeContext], dict[str, Any]]] = {
    "anthropic": _anthropic_cache,
    "openai": _openai_cache,
}


def apply_caching(kwargs: dict[str, Any], ctx: ShapeContext) -> dict[str, Any]:
    if not ctx.caching:
        return kwargs
    cacher = _CACHERS.get(family_of(ctx.model))
    return cacher(kwargs, ctx) if cacher else kwargs


def remap(kwargs: dict[str, Any], ctx: ShapeContext) -> dict[str, Any]:
    out = dict(kwargs)
    omit = set(ctx.omit) | set(ctx.variant.omit_fields if ctx.variant else ())
    for f in omit:
        out.pop(f, None)
    for old, new in (ctx.variant.rename_fields if ctx.variant else {}).items():
        if old in out:
            out[new] = out.pop(old)
    return out


PIPELINE: tuple[tuple[str, Callable[[dict[str, Any], ShapeContext], dict[str, Any]]], ...] = (
    ("unsupported_parts", unsupported_parts),
    ("normalize", normalize),
    ("apply_caching", apply_caching),
    ("remap", remap),
)


def shape(kwargs: dict[str, Any], ctx: ShapeContext) -> dict[str, Any]:
    for _name, step in PIPELINE:
        kwargs = step(kwargs, ctx)
    return kwargs


__all__ = [
    "PIPELINE",
    "effort_param",
    "idle_chunks",
    "literal_values",
    "lookup_names",
    "provider_headers",
    "provider_of",
    "raw_body",
    "ShapeContext",
    "clamp_max_tokens",
    "family_of",
    "is_opus_class",
    "load_variants",
    "map_effort",
    "match_variant",
    "reasoning_kwargs",
    "resolve_effort",
    "shape",
]
