"""Structured output contract (spec §25.9 L1003). No `litellm` here.

`normalize_response_format` turns a Pydantic model class or a loose dict into the
`{type: json_schema, json_schema: {name, schema, strict: true}}` envelope; `check_support`
fails fast with `FORMAT` when no deployment of the target can honour a JSON schema (no
silent downgrade). The capability predicate is injected by the gateway (LiteLLM's
`supports_response_schema`).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from auoreo.gateway.errors import GatewayError

_NAME = re.compile(r"[^A-Za-z0-9_-]")


def normalize_response_format(fmt: Any) -> dict[str, Any] | None:
    """None → None; a BaseModel class → strict json_schema; `json_object`/`text` dicts pass
    through; a bare JSON Schema dict is wrapped."""
    if fmt is None:
        return None
    schema_fn = getattr(fmt, "model_json_schema", None)
    if callable(schema_fn):
        name = _NAME.sub("_", getattr(fmt, "__name__", "response"))[:64] or "response"
        return {"type": "json_schema", "json_schema": {"name": name, "schema": schema_fn(), "strict": True}}
    if not isinstance(fmt, dict):
        raise GatewayError("FORMAT", f"response_format must be a dict or a Pydantic model, not {type(fmt)}")
    if fmt.get("type") in {"json_object", "text"}:
        return dict(fmt)
    if fmt.get("type") == "json_schema":
        inner = dict(fmt.get("json_schema") or {})
        if not isinstance(inner.get("schema"), dict):
            raise GatewayError("FORMAT", "response_format.json_schema.schema must be a JSON Schema object")
        inner.setdefault("name", "response")
        inner.setdefault("strict", True)
        return {"type": "json_schema", "json_schema": inner}
    if "properties" in fmt or fmt.get("type") == "object":  # a bare JSON Schema
        return {"type": "json_schema", "json_schema": {"name": "response", "schema": fmt, "strict": True}}
    raise GatewayError("FORMAT", "response_format: expected json_schema, json_object, text or a schema")


def needs_schema(fmt: dict[str, Any] | None) -> bool:
    return bool(fmt) and fmt.get("type") == "json_schema"  # type: ignore[union-attr]


def check_support(
    fmt: dict[str, Any] | None,
    deployments: list[str],
    supports: Callable[[str], bool],
    *,
    fallback_structured: bool = False,
) -> bool:
    """True when the request may go out as-is. Raises `FORMAT` when a json_schema request
    targets deployments that cannot honour it; with `fallback_structured` returns False
    instead (the caller downgrades to `json_object` + schema-in-prompt for ONE aux retry)."""
    if not needs_schema(fmt):
        return True
    unsupported = [d for d in deployments if not supports(d)]
    if not unsupported:
        return True
    if fallback_structured:
        return False
    raise GatewayError(
        "FORMAT",
        f"{', '.join(unsupported)} cannot honour response_format json_schema (no silent downgrade); "
        "pick a model with structured-output support or set i2.fallback_structured",
    )


def downgrade(
    fmt: dict[str, Any], messages: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """`fallback_structured`: json_object mode + the schema appended as a system note."""
    import json

    schema = (fmt.get("json_schema") or {}).get("schema") or {}
    note = {
        "role": "system",
        "content": f"Reply with ONE JSON object matching this schema:\n{json.dumps(schema)}",
    }
    return {"type": "json_object"}, [*messages, note]


__all__ = ["check_support", "downgrade", "needs_schema", "normalize_response_format"]
