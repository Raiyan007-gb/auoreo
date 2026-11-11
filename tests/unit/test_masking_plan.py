"""Observation masking, the pure plan (M7 W1 Wave 2): what a compaction run may mask."""

from __future__ import annotations

from typing import Any

import pytest

from auoreo.core.settings_m7 import M7Mask
from auoreo.runtime.masking_plan import (
    MASK_PREFIX,
    PLACEHOLDER_CHARS,
    is_placeholder,
    plan,
    spill_name,
)
from auoreo.tools.read_dedupe import NOTICE_PREFIX

CFG = M7Mask()


def pair(i: int, name: str, size: int, content: str | None = None) -> list[dict[str, Any]]:
    call = {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": "{}"}}
    body = content if content is not None else "x" * size
    return [
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "tool_call_id": f"c{i}", "content": body},
    ]


def recent(n: int = 10, start: int = 100) -> list[dict[str, Any]]:
    return [m for i in range(start, start + n) for m in pair(i, "bash", 50)]


def test_protected_head_and_recent_tail() -> None:
    hist: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    hist += pair(0, "read", 5000)  # index 2 < PRUNE_KEEP_HEAD: protected
    hist += pair(1, "read", 5000)  # index 4: masked
    hist += recent()  # the last 10 tool results are kept
    p = plan(hist, CFG)
    assert p.indices == (4,) and bool(p)
    assert p.tokens == (5000 - PLACEHOLDER_CHARS) // 4
    assert p.suffix_tokens > 0


def test_allowlist_size_and_already_masked() -> None:
    hist: list[dict[str, Any]] = [{"role": "user", "content": c} for c in "abc"]
    hist += pair(0, "skill", 5000)  # not in PRUNABLE_TOOLS
    hist += pair(1, "bash", 1999)  # below min_chars
    hist += pair(2, "grep", 5000, content=MASK_PREFIX + "x" * 5000)  # already a placeholder
    hist += pair(3, "web_fetch", 2000)  # exactly min_chars: masked
    hist += recent()
    assert plan(hist, CFG).indices == (10,)
    assert is_placeholder(hist[8]) and not is_placeholder(hist[10])


def test_keep_recent_and_min_chars_are_configurable() -> None:
    hist: list[dict[str, Any]] = [{"role": "user", "content": c} for c in "abc"]
    hist += pair(0, "read", 500) + pair(1, "read", 500)
    assert not plan(hist, CFG)
    assert plan(hist, M7Mask(keep_recent=0, min_chars=100)).indices == (4, 6)


def test_suffix_counts_everything_after_the_first_masked_message() -> None:
    hist: list[dict[str, Any]] = [{"role": "user", "content": c} for c in "abc"]
    hist += pair(0, "read", 8000) + [{"role": "user", "content": "y" * 4000}]
    p = plan(hist, M7Mask(keep_recent=0))
    assert p.indices == (4,)
    assert p.suffix_tokens == (8000 + 4000) // 4 - p.tokens


def test_stale_dedupe_notices_in_the_same_turn_are_selected() -> None:
    notice = f"{NOTICE_PREFIX} /a.py (offset 0, limit 2000) has not changed"
    hist: list[dict[str, Any]] = [{"role": "user", "content": c} for c in "abc"]
    hist += pair(0, "read", 5000)  # 4: masked read
    hist += pair(1, "read", 0, content=notice)  # 6: notice in the same turn: rewritten
    hist += [{"role": "user", "content": "next turn"}]
    hist += pair(2, "read", 0, content=notice)  # 9: a later turn's notice refers to its own read
    p = plan(hist, M7Mask(keep_recent=0))
    assert p.indices == (4,) and p.notices == (6,)


def test_notices_follow_only_read_results() -> None:
    notice = f"{NOTICE_PREFIX} /a.py"
    hist: list[dict[str, Any]] = [{"role": "user", "content": c} for c in "abc"]
    hist += pair(0, "bash", 5000) + pair(1, "read", 0, content=notice)
    assert plan(hist, M7Mask(keep_recent=0)).notices == ()


@pytest.mark.parametrize(
    ("call_id", "safe"),
    [
        ("toolu_01AbC-9", True),
        ("call_abc123", True),
        ("CON", False),
        ("nul", False),
        ("com1", False),
        ("lpt9", False),
        ("a/b", False),
        ("a:b", False),
        ("..", False),
        ("x" * 97, False),
        ("", False),
        ("é", False),
    ],
)
def test_spill_names_are_windows_safe(call_id: str, safe: bool) -> None:
    name = spill_name(call_id)
    assert (name == call_id) is safe
    if not safe:
        assert name.startswith("h_") and len(name) == 18 and name[2:].isalnum()
    assert spill_name(call_id) == name  # deterministic
