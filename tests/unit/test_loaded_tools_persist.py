"""Deferred tools surfaced by `tool_search` survive a reopened session (another process)."""

from __future__ import annotations

from auoreo.tools.context import TOOLS_LOADED_PREFIX, loaded_from_history


def test_loaded_from_history_parses_tool_search_results() -> None:
    history = [
        {"role": "user", "content": "find goal tools"},
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": f"{TOOLS_LOADED_PREFIX}\n- create_goal: Create a goal.\n- get_goal: Read goals.",
        },
        {"role": "tool", "tool_call_id": "c2", "content": "- not_loaded: unrelated tool output"},
        {"role": "assistant", "content": f"{TOOLS_LOADED_PREFIX}\n- fake: assistant text never counts"},
    ]
    assert loaded_from_history(history) == {"create_goal", "get_goal"}


def test_restore_state_rebuilds_plan_verify_and_mode() -> None:
    from auoreo.tools.context import SessionState, restore_state
    from auoreo.tools.testrun import plan_hash

    steps = [{"step": "fix it", "status": "pending", "done_when": "tests pass"}]
    digest = plan_hash(steps)

    def call(cid: str, name: str, args: str) -> dict:
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": args}}],
        }

    import json as _json

    history = [
        call("a", "enter_plan_mode", "{}"),
        {"role": "tool", "tool_call_id": "a", "content": "Plan mode is on. Edits are blocked"},
        call("b", "update_plan", _json.dumps({"steps": steps})),
        {"role": "tool", "tool_call_id": "b", "content": "plan updated:\n1. [pending] fix it"},
        call("c", "verify_plan", "{}"),
        {"role": "tool", "tool_call_id": "c", "content": f"verify_plan: PASS\n[x] ok\nplan hash: {digest}"},
    ]
    st = SessionState()
    restore_state(st, history)
    assert (st.plan, st.plan_verified, st.plan_mode) == (steps, digest, True)
    history += [
        call("d", "exit_plan_mode", "{}"),
        {"role": "tool", "tool_call_id": "d", "content": "Plan approved; plan mode is off."},
        call("e", "verify_plan", "{}"),
        {"role": "tool", "tool_call_id": "e", "content": "verify_plan: FAIL — fix\nplan hash: x"},
    ]
    st = SessionState()
    restore_state(st, history)
    assert (st.plan_mode, st.plan_verified) == (False, None)
