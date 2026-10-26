"""Tool schema shaper (spec §25.7 L979, §9.1 `strict=True` default). No `litellm` here.

`shape_for_model(model, tools)` returns a shaped DEEP COPY of OpenAI-style function tools
for one provider family (dict dispatch on `pipeline.family_of`); the canonical schemas the
executor validates against are never mutated.

- `openai`: `strict: true` when the schema is strict-compatible (every property required,
  `additionalProperties: false` on every object); otherwise sent non-strict.
- `anthropic`: `strict` omitted; schema passed through (enums clamped).
- `gemini`: `strict` stripped, unions flattened, keywords Gemini rejects dropped.
- anything else (OpenRouter long tail, local llama.cpp/vLLM/Ollama): most restrictive —
  strip `strict`, flatten unions, drop non-core keywords, clamp enums.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from auoreo.gateway.pipeline import family_of

DEFAULT_MAX_ENUM = 64
_GEMINI_DROP = frozenset({"additionalProperties", "$schema", "$id", "examples", "default", "title", "const"})
_STRICT_DROP = _GEMINI_DROP | frozenset({"format", "pattern", "minLength", "maxLength", "patternProperties"})
_UNIONS = ("anyOf", "oneOf")


def _walk(schema: Any, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> Any:
    """Apply `fn` bottom-up to every sub-schema dict."""
    if isinstance(schema, list):
        return [_walk(s, fn) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k == "properties" and isinstance(v, dict):
            out[k] = {name: _walk(sub, fn) for name, sub in v.items()}
        elif k in {"items", "additionalProperties", *_UNIONS, "allOf", "$defs", "definitions"}:
            out[k] = (
                {n: _walk(s, fn) for n, s in v.items()} if k in {"$defs", "definitions"} else _walk(v, fn)
            )
        else:
            out[k] = v
    return fn(out)


def _clamp_enum(max_enum: int) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def step(node: dict[str, Any]) -> dict[str, Any]:
        enum = node.get("enum")
        if isinstance(enum, list) and len(enum) > max_enum:
            node = {**node, "enum": enum[:max_enum]}
            extra = f" (only the first {max_enum} of {len(enum)} values are listed)"
            node["description"] = str(node.get("description", "")) + extra
        return node

    return step


def _flatten_union(node: dict[str, Any]) -> dict[str, Any]:
    """`anyOf/oneOf [X, null]` → X (nullable); other unions → the first object-ish branch."""
    for key in _UNIONS:
        branches = node.get(key)
        if not isinstance(branches, list) or not branches:
            continue
        rest = {k: v for k, v in node.items() if k != key}
        real = [b for b in branches if not (isinstance(b, dict) and b.get("type") == "null")]
        pick = real[0] if real else {"type": "string"}
        merged = {**(pick if isinstance(pick, dict) else {}), **rest}
        if len(real) < len(branches):
            merged["nullable"] = True
        if len(real) > 1:
            merged["description"] = (str(merged.get("description", "")) + " (one of several shapes)").strip()
        return merged
    return node


def _drop(keys: frozenset[str]) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def step(node: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in node.items() if k not in keys}

    return step


def strict_compatible(schema: Any) -> bool:
    """OpenAI strict mode: every object lists all properties as required and forbids extras."""
    if isinstance(schema, list):
        return all(strict_compatible(s) for s in schema)
    if not isinstance(schema, dict):
        return True
    if schema.get("type") == "object" or "properties" in schema:
        props = schema.get("properties") or {}
        if schema.get("additionalProperties") is not False or set(schema.get("required") or []) != set(props):
            return False
    subs = [*(schema.get("properties") or {}).values(), schema.get("items")]
    subs += [*(schema.get("anyOf") or []), *(schema.get("$defs") or {}).values()]
    return all(strict_compatible(s) for s in subs if s is not None) and "oneOf" not in schema


def _fn(tool: dict[str, Any]) -> dict[str, Any]:
    return tool.get("function") if isinstance(tool.get("function"), dict) else {}  # type: ignore[return-value]


def _openai(tool: dict[str, Any], strict: bool, max_enum: int) -> dict[str, Any]:
    fn = _fn(tool)
    params = _walk(fn.get("parameters") or {}, _clamp_enum(max_enum))
    fn = {**fn, "parameters": params}
    if strict and strict_compatible(params):
        fn["strict"] = True
    else:
        fn.pop("strict", None)
    return {**tool, "function": fn}


def _anthropic(tool: dict[str, Any], strict: bool, max_enum: int) -> dict[str, Any]:
    fn = {k: v for k, v in _fn(tool).items() if k != "strict"}
    fn["parameters"] = _walk(fn.get("parameters") or {}, _clamp_enum(max_enum))
    return {**tool, "function": fn}


def _restrictive(drop: frozenset[str]) -> Callable[[dict[str, Any], bool, int], dict[str, Any]]:
    def shaper(tool: dict[str, Any], strict: bool, max_enum: int) -> dict[str, Any]:
        fn = {k: v for k, v in _fn(tool).items() if k != "strict"}
        clamp, dropper = _clamp_enum(max_enum), _drop(drop)
        params = _walk(fn.get("parameters") or {}, lambda n: dropper(clamp(_flatten_union(n))))
        fn["parameters"] = params
        return {**tool, "function": fn}

    return shaper


_SHAPERS: dict[str, Callable[[dict[str, Any], bool, int], dict[str, Any]]] = {
    "openai": _openai,
    "anthropic": _anthropic,
    "gemini": _restrictive(_GEMINI_DROP),
    "kimi": _restrictive(_STRICT_DROP),
    "default": _restrictive(_STRICT_DROP),  # unknown combo = most-restrictive fail-safe
}


def shape_for_model(
    model: str,
    tools: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    strict: bool = True,
    max_enum: int = DEFAULT_MAX_ENUM,
) -> list[dict[str, Any]]:
    """Shaped copies of `tools` for `model`'s family; non-function tools pass through."""
    shaper = _SHAPERS.get(family_of(model), _SHAPERS["default"])
    out = []
    for tool in tools:
        tool = copy.deepcopy(tool)
        out.append(shaper(tool, strict, max_enum) if tool.get("type") == "function" else tool)
    return out


__all__ = ["shape_for_model", "strict_compatible"]
