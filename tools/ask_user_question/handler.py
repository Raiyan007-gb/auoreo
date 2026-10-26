import json

from auoreo.tools.context import ToolContext, ToolResult

DEFAULT_TIMEOUT_S = 60  # spec §25.7: silence for 60 s → `timed_out`, the agent picks a default
TIMED_OUT = "The user did not answer in time. Proceed with a sensible default and say which one you chose."


def _shape(questions: list[dict]) -> list[dict]:
    """`allowCustom` defaults to true; single-select unless `multiSelect`."""
    return [
        {**q, "allowCustom": q.get("allowCustom", True), "multiSelect": q.get("multiSelect", False)}
        for q in questions
    ]


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    questions = _shape(args["questions"])
    answer = await ctx.question(questions, timeout_s=args.get("timeout_s", DEFAULT_TIMEOUT_S))
    if answer.get("timed_out"):
        payload = {"responses": answer.get("responses", []), "timed_out": True}
        return ToolResult(f"{TIMED_OUT}\n{json.dumps(payload, ensure_ascii=False)}", is_error=True)
    responses = [
        {k: r[k] for k in ("question", "answers", "customText") if k in r}
        for r in answer.get("responses", [])
    ]
    return ToolResult(json.dumps({"responses": responses, "timed_out": False}, ensure_ascii=False))
