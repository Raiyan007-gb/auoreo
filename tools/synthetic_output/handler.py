"""synthetic_output (spec §16.5 coordinator allowlist): the coordinator's structured final
output. Validated against the `output_schema` this session was delegated with (if any); the
recorded value replaces the child's final text when its turn ends (`teams.child_run`)."""

import json

from auoreo.teams.format import SCHEMA_MISMATCH, check_value
from auoreo.tools.context import ToolContext, ToolError, ToolResult

MAX_BYTES = 64 * 1024


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    output = args["output"]
    blob = json.dumps(output, ensure_ascii=False)
    if len(blob.encode("utf-8")) > MAX_BYTES:
        raise ToolError(f"synthetic_output is larger than {MAX_BYTES} bytes; put the bulk on the board")
    svc = ctx.services.get("services")
    teams = svc.ext.get("teams") if svc is not None else None
    member = teams.members_live.get(ctx.session_id) if teams is not None else None
    schema = getattr(member, "schema", None)
    if schema and (problem := check_value(output, schema)) is not None:
        raise ToolError(f"{SCHEMA_MISMATCH}: {problem}. Fix the object and call synthetic_output again.")
    if teams is not None:
        teams.synthetic[ctx.session_id] = output
    await ctx.emit("synthetic.output", {"bytes": len(blob), "validated": bool(schema)})
    return ToolResult(
        "structured output recorded; finish now with a one-line note (the object is your answer)",
        meta={"synthetic_output": output, "validated": bool(schema)},
    )
