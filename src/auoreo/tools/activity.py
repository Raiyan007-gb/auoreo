"""Tool `activityHint` + `progressSchema` (spec §25.7 L977 progress contract).

`activity_hint` is a short template rendered from the call's arguments (`Reading {path}`):
`{name}` placeholders take the argument's text (40 chars max), unknown ones stay literal, the
whole label is capped at 80 characters. It rides on `tool.started` as `activity` and is listed
in the harness listing. `progress_schema` validates the `data` of `ctx.progress(..., data=…)`.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

log = logging.getLogger(__name__)
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
MAX_LABEL, MAX_VALUE = 80, 40


def render_activity(hint: str, arguments: str | dict[str, Any]) -> str:
    if not hint:
        return ""
    args: Any = arguments
    if isinstance(arguments, str):
        try:
            args = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            args = {}
    args = args if isinstance(args, dict) else {}

    def sub(m: re.Match[str]) -> str:
        value = args.get(m[1])
        if value is None:
            return m[0]
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        text = " ".join(text.split())
        return text if len(text) <= MAX_VALUE else text[: MAX_VALUE - 1] + "…"

    label = PLACEHOLDER.sub(sub, hint)
    return label if len(label) <= MAX_LABEL else label[: MAX_LABEL - 1] + "…"


def activity_fields(tool: Any, arguments: str) -> dict[str, str]:
    """`{"activity": label}` for `tool.started` when the tool declares an `activity_hint`."""
    hint = getattr(getattr(tool, "manifest", None), "activity_hint", "") or ""
    label = render_activity(hint, arguments)
    return {"activity": label} if label else {}


def progress_error(schema: dict[str, Any] | None, data: Any) -> str | None:
    """None when `data` satisfies the tool's `progress_schema` (or there is none)."""
    if schema is None:
        return None
    err = best_match(Draft202012Validator(schema).iter_errors(data))
    if err is None:
        return None
    pointer = "/" + "/".join(str(p) for p in err.absolute_path)
    return f"progress data invalid at {pointer}: {err.message}"


__all__ = ["activity_fields", "progress_error", "render_activity"]
