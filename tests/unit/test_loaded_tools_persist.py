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
