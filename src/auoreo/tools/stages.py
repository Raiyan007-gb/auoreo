"""Pure stages of the tool pipeline, kept out of `executor.py` (spec §9.3–§9.4):
result pruning + capped spill, `output_schema` checks, the handler's `pre_check` floor,
and the small per-call helpers (agent name, policy context, approval detail).
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jsonschema

from auoreo.core.redaction import redact
from auoreo.harness.loader import LoadError
from auoreo.tools.args import format_validation_error
from auoreo.tools.context import ToolResult

if TYPE_CHECKING:
    from auoreo.harness.loader import ToolDef
    from auoreo.tools.context import ToolContext

SPILL_DEFAULT = 64 * 1024 * 1024


def _write_spill(path: Path, text: str, cap: int) -> tuple[int, bool]:
    data = text.encode("utf-8")
    if len(data) <= cap:
        path.write_bytes(data)
        return len(data), False
    marker = f"\n… [spill truncated at {cap} of {len(data)} bytes] …\n".encode()
    keep = data[: max(0, cap - len(marker))].decode("utf-8", errors="ignore").encode("utf-8")
    path.write_bytes(keep + marker)
    return len(data), True


def prune(
    text: str, limit: int, spill_dir: Path, call_id: str, spill_cap: int = SPILL_DEFAULT
) -> tuple[str, dict[str, Any]]:
    """Keep results under `limit` chars; spill the full text (≤ `spill_cap` bytes) to disk."""
    if len(text) <= limit:
        return text, {}
    spill_dir.mkdir(parents=True, exist_ok=True)
    path = spill_dir / f"{call_id}.txt"
    size, cut = _write_spill(path, text, spill_cap)
    head = text[: limit * 3 // 4]
    tail = text[-limit // 8 :]
    where = f"full output: {path}" if not cut else f"first {spill_cap} of {size} bytes kept in {path}"
    note = f"\n… [{len(text) - len(head) - len(tail)} chars truncated; {where}] …\n"
    meta: dict[str, Any] = {"truncated": True, "output_path": str(path)}
    if cut:
        meta["spill_truncated"] = True
    return head + note + tail, meta


def check_output(tool: ToolDef, result: ToolResult) -> ToolResult:
    """Validate a structured result against the manifest's `output_schema`: the value is
    `meta["structured"]` when present, else the content parsed as JSON."""
    schema = tool.manifest.output_schema
    if schema is None or result.is_error:
        return result
    if "structured" in result.meta:
        value = result.meta["structured"]
    else:
        try:
            value = json.loads(result.content)
        except json.JSONDecodeError:
            return ToolResult(f"tool output is not JSON but '{tool.name}' declares an output_schema", True)
    try:
        jsonschema.validate(value, schema)
    except jsonschema.ValidationError as exc:
        msg = format_validation_error(exc).replace("invalid arguments", "invalid output", 1)
        return ToolResult(f"'{tool.name}' returned {msg}", True, result.meta)
    return ToolResult(result.content, False, {**result.meta, "structured": value}, images=result.images)


def pre_check(tool: ToolDef, args: dict[str, Any]) -> str | None:
    """The handler module's optional `pre_check(args) -> str | None` (folder tools only): a
    non-None reason refuses the call before permission evaluation, so a floor-blocked call
    never raises an approval prompt. Import failures are left to the execute stage."""
    if tool.path is None:
        return None
    try:
        module = inspect.getmodule(tool.handler())
    except LoadError:
        return None
    check = getattr(module, "pre_check", None)
    return check(args) if callable(check) else None


def agent_of(ctx: ToolContext) -> str:
    spec = ctx.services.get("spec")
    return str(getattr(spec, "name", "") or "")


def policy_context(tool: ToolDef, ctx: ToolContext) -> dict[str, Any]:
    """The per-call fields of the exec-policy `req` (spec §2.10)."""
    out: dict[str, Any] = {"tool": tool.name, "session_id": ctx.session_id, "cwd": str(ctx.cwd)}
    if agent := agent_of(ctx):
        out["agent"] = agent
    return out


def detail(tool: ToolDef, args: dict[str, Any]) -> str:
    text = json.dumps(args, ensure_ascii=False)
    return redact(text if len(text) < 2000 else text[:2000] + "…")
