"""Lossless tool arguments (spec §9.4, §25.7 "Lossless args").

`snapshot_json_value(raw)` parses the model's raw JSON once and returns two detached deep
copies: `dispatched` (handed to the handler, which may mutate it) and `logged` (hooks,
audit, approval cards). Anything that cannot round-trip exactly is rejected with a typed
`InvalidArgumentsError`, never silently coerced: duplicate keys (a dict keeps only the
last), `NaN`/`Infinity`, integers beyond ±(2^53−1) (pass big numbers as strings), and a
non-object top level.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any

import jsonschema

MAX_SAFE_INTEGER = 2**53 - 1


class InvalidArgumentsError(ValueError):
    """Stage-1 shape failure; its prose goes back to the model as an `is_error` result."""

    def __init__(self, tool: str, detail: str) -> None:
        self.tool = tool
        self.detail = detail
        super().__init__(f"InvalidArgumentsError: {tool}: {detail}")


@dataclass(frozen=True)
class ArgSnapshot:
    dispatched: dict[str, Any]
    logged: dict[str, Any]


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


def _int(text: str) -> int:
    value = int(text)
    if abs(value) > MAX_SAFE_INTEGER:
        raise ValueError(f"integer {text[:24]} exceeds 2^53-1; pass it as a string")
    return value


def _constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def snapshot_json_value(tool: str, raw: str | None) -> ArgSnapshot:
    try:
        value = json.loads(raw or "{}", object_pairs_hook=_pairs, parse_int=_int, parse_constant=_constant)
    except json.JSONDecodeError as exc:
        raise InvalidArgumentsError(tool, f"arguments are not valid JSON: {exc.msg}") from None
    except (ValueError, RecursionError) as exc:
        raise InvalidArgumentsError(tool, f"arguments are not lossless JSON: {exc}") from None
    if not isinstance(value, dict):
        raise InvalidArgumentsError(tool, "arguments must be a JSON object")
    return ArgSnapshot(value, copy.deepcopy(value))


def format_validation_error(err: jsonschema.ValidationError) -> str:
    where = "/".join(str(p) for p in err.absolute_path) or "(root)"
    return f"invalid arguments at {where}: {err.message}"


def validate_args(tool: str, args: dict[str, Any], schema: dict[str, Any], prefix: str = "") -> None:
    """JSON-Schema check (no coercion); raises `InvalidArgumentsError` with the schema error."""
    try:
        jsonschema.validate(args, schema)
    except jsonschema.ValidationError as exc:
        raise InvalidArgumentsError(tool, prefix + format_validation_error(exc)) from None
