"""Model-facing text for teams: task notifications, mail envelopes, output schemas."""

from __future__ import annotations

import json
from typing import Any
from xml.sax.saxutils import escape, quoteattr

import jsonschema

SCHEMA_MISMATCH = "TOOL_OUTPUT_SCHEMA_MISMATCH"
_TN = ("task-notification", "result", "summary")


def _body(text: str, *tags: str) -> str:
    """Keep the body readable (no entity escaping) but unable to close the envelope."""
    for tag in tags:
        text = text.replace(f"</{tag}", f"<\\/{tag}")
    return text


def summarize(text: str, limit: int = 80) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def task_notification(task_id: str, status: str, summary: str, result: str, usage: dict[str, Any]) -> str:
    usage_text = (
        f"prompt_tokens={usage.get('prompt_tokens', 0)} "
        f"completion_tokens={usage.get('completion_tokens', 0)} "
        f"cost_usd={float(usage.get('cost_usd') or 0.0):.6f}"
    )
    return (
        "<task-notification>\n"
        f"<task-id>{escape(task_id)}</task-id>\n"
        f"<status>{escape(status)}</status>\n"
        f"<summary>{_body(summary, *_TN)}</summary>\n"
        f"<result>{_body(result, *_TN)}</result>\n"
        f"<usage>{usage_text}</usage>\n"
        "</task-notification>"
    )


def mail_envelope(sender: str, summary: str, text: str) -> str:
    return f"<mail from={quoteattr(sender)} summary={quoteattr(summary)}>{_body(text, 'mail')}</mail>"


def schema_instruction(schema: dict[str, Any]) -> str:
    return (
        "Your final answer must be ONLY a JSON value (no prose, no code fences) that "
        f"validates against this JSON Schema:\n{json.dumps(schema, indent=2)}"
    )


def retry_instruction(error: str, schema: dict[str, Any]) -> str:
    return (
        f"{SCHEMA_MISMATCH}: your previous answer did not match the required schema ({error}). "
        + schema_instruction(schema)
    )


def _unfence(text: str) -> str:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        raw = raw.removeprefix("json").strip()
    return raw


def parse_json(text: str) -> Any:
    """The JSON value of a (possibly fenced) final answer, or None."""
    try:
        return json.loads(_unfence(text))
    except json.JSONDecodeError:
        return None


def check_value(value: Any, schema: dict[str, Any]) -> str | None:
    """None when `value` is valid for `schema`, else the error message."""
    try:
        jsonschema.validate(value, schema)
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "(root)"
        return f"at {where}: {exc.message}"
    except jsonschema.SchemaError as exc:
        return f"output_schema is not a valid JSON Schema: {exc.message}"
    return None


# --- I3: handoff schema for child completions (spec §25.11)
HANDOFF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "purpose": {"type": "string", "minLength": 1},
        "files": {"type": "array", "items": {"type": "string", "pattern": r"^[^\s:][^:]*:\d+(-\d+)?$"}},
        "done": {"type": "boolean"},
        "commit_hash": {"type": "string", "pattern": "^[0-9a-f]{7,40}$"},
        "summary": {"type": "string", "minLength": 1, "maxLength": 80},
        "usage": {"type": "object"},
    },
    "required": ["purpose", "files", "done", "summary"],
}
"""`{purpose, files:[path:line], done, commit_hash?, summary≤80ch, usage}`; `usage` is filled
in by the harness from the child's ledger after validation."""


def check_output(text: str, schema: dict[str, Any]) -> str | None:
    """None when `text` parses as JSON valid for `schema`, else the error message."""
    raw = _unfence(text)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        return f"not valid JSON: {exc.msg}"
    try:
        jsonschema.validate(value, schema)
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "(root)"
        return f"at {where}: {exc.message}"
    except jsonschema.SchemaError as exc:
        return f"output_schema is not a valid JSON Schema: {exc.message}"
    return None
