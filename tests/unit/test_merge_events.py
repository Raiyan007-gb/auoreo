"""M7 W4 Wave 2, phase 1: merge settings, the `review.*`/`merge.*`/`worktree.*` event models
and their levels, and `task_ref` (the small string form)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from auoreo.core.event_levels import level_of
from auoreo.core.settings_m7 import M7Orch, M7Settings
from auoreo.runtime.worktrees import NewFeature
from auoreo.schema import events as ev
from auoreo.teams import worktree_spawn


def test_merge_settings_default_off_and_tolerant() -> None:
    orch = M7Orch()
    assert orch.merge is False and orch.merge_definitions == "refuse"
    assert orch.merge_max_objects == 20000 and orch.conflict_board is None
    assert orch.reconcile_interval_s == 300
    assert M7Settings.model_validate({"orch": {"some_future_key": 1}}).orch.merge is False
    with pytest.raises(ValidationError, match="m7.orch.merge requires m7.orch.worktrees"):
        M7Orch(merge=True, session_cwd=True)
    assert M7Orch(merge=True, worktrees=True, session_cwd=True).merge
    with pytest.raises(ValidationError):
        M7Orch(merge_definitions="allow")  # type: ignore[arg-type]


def test_event_models_validate() -> None:
    ev.ReviewRequested(
        branch="auoreo/x", sha="a" * 40, base="b" * 40, commits=1, files=2, task_ref="kanban:d#1"
    )
    ev.ReviewDecision(branch="auoreo/x", sha="a" * 40, target="main", target_sha="c" * 40, request_id="r")
    ev.MergeDenied(branch="auoreo/x", sha="a" * 40, reason="sha_mismatch")
    conflicts = ev.MergeConflicts(
        branch="auoreo/x", sha="a" * 40, target="main", target_sha="c" * 40, files=["f"] * 50, conflict_task=3
    )
    assert conflicts.task_ref is None
    with pytest.raises(ValidationError):
        ev.MergeConflicts(branch="b", sha="s", target="t", target_sha="h", files=["f"] * 51, conflict_task=3)
    ev.MergeCompleted(branch="auoreo/x", sha="a" * 40, target="main", target_sha="c" * 40, merge_sha="d" * 40)
    ev.MergeFailed(branch="auoreo/x", sha="a" * 40, message="boom")
    ev.WorktreeRemoved(path="/w/x", branch="auoreo/x", reason="merged")
    with pytest.raises(ValidationError):
        ev.WorktreeRemoved(path="/w/x", reason="because")  # type: ignore[arg-type]
    ev.WorktreeGc(removed=1, pruned=0, branches_deleted=2, kept=3)


def test_event_levels() -> None:
    assert level_of("merge.conflicts") == "warn"
    assert level_of("merge.denied") == "warn"
    assert level_of("review.rejected") == "warn"
    assert level_of("merge.failed") == "error"
    for name in ("merge.completed", "review.requested", "review.approved", "worktree.removed", "worktree.gc"):
        assert level_of(name) == "info"


def test_task_ref_forms_and_worktree_created_payload() -> None:
    assert worktree_spawn.kanban_ref("default", 12) == "kanban:default#12"
    assert worktree_spawn.delegate_ref("ses_1", "fix") == "delegate:ses_1/fix"
    tree = NewFeature(Path("/w/kdefault-12"), "auoreo/kdefault-12", "a" * 40)
    data = worktree_spawn.payload(tree, task_id=12, board="default")
    assert data["task_ref"] == "kanban:default#12" and data["task_id"] == 12
    plain = worktree_spawn.payload(tree, task_ref="delegate:ses_1/fix")
    assert plain["task_ref"] == "delegate:ses_1/fix" and "task_id" not in plain
    assert "task_ref" not in worktree_spawn.payload(tree)


def test_git_merge_is_hidden_while_the_flag_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from auoreo.teams import merge_flow

    off = SimpleNamespace(m7=M7Settings())
    on = SimpleNamespace(m7=M7Settings(orch=M7Orch(session_cwd=True, worktrees=True, merge=True)))
    monkeypatch.setattr("auoreo.core.settings.get_settings", lambda: off)
    assert merge_flow.available() == "m7.orch.merge is off"
    monkeypatch.setattr("auoreo.core.settings.get_settings", lambda: on)
    assert merge_flow.available() is True
