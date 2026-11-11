"""M7 W4 Wave 2, phase 3: the `git_merge` tool through the executor. The approval lives in the
handler: human-only, one-shot, bound to (repository, source sha, target branch + HEAD), and no
rule, permission mode, hook or guardian can grant it. The source branch is untrusted."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from auoreo.core import permissions as perm
from auoreo.core.db import AuditRow
from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.harness.manifests import Rule
from tests.conftest import write
from tests.merge_helpers import Setup, call, card, commit, events, recorded_worktree
from tests.worktree_helpers import git

posix = pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell script")


@pytest.fixture(autouse=True)
def _merge_tool_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    """`check_fn` reads the process settings; these tests turn the flag on per `Services`."""
    from auoreo.teams import merge_flow

    monkeypatch.setattr(merge_flow, "available", lambda: True)


@pytest.fixture
async def setup(services: Services, workdir: Path) -> Setup:
    return await recorded_worktree(services)


def _args(s: Setup, sha: str, **extra: str) -> dict[str, str]:
    return {"branch": s.tree.branch, "sha": sha, **extra}


async def _approve(s: Setup, args: dict[str, str], reply: str = "once", **kw: object) -> tuple[str, bool]:
    task = asyncio.create_task(call(s.svc, args, **kw))  # type: ignore[arg-type]
    pending = await card(s.svc)
    await s.svc.approvals.reply(pending.request_id, reply)  # type: ignore[arg-type]
    res = await task
    return res.content, res.is_error


def _head(s: Setup) -> str:
    return git(s.repo, "rev-parse", "HEAD")


async def _audit(svc: Services) -> list[str]:
    async with svc.db.session() as db:
        return [r.action for r in (await db.execute(select(AuditRow).order_by(AuditRow.seq))).scalars()]


# ------------------------------------------------------------------ the happy path
async def test_approved_merge(setup: Setup) -> None:
    s = setup
    head = _head(s)
    sha = commit(s.tree.path, {"src/fetch.py": "print('retry')\n"}, "Add retry to fetcher")
    task = asyncio.create_task(call(s.svc, _args(s, sha, note="reviewer said: tests pass")))
    pending = await card(s.svc)
    assert pending.one_shot and pending.risk == "high" and pending.permission == "git_merge"
    assert pending.pattern == f"{s.tree.branch}@{sha}->main@{head}"
    text = pending.detail
    assert f"merge   {s.tree.branch} -> main" in text and sha in text and "Add retry to fetcher" in text
    assert head[:12] in text and "kanban:default#1" in text and "(agent, not verified)" in text
    assert "src/fetch.py +1 -0" in text and "print(" not in text  # file names and counts, never the diff
    assert len(text) <= 2000
    await s.svc.approvals.reply(pending.request_id, "once")
    res = await task
    assert not res.is_error, res.content
    merged = _head(s)
    assert git(s.repo, "rev-parse", f"{merged}^1") == head and git(s.repo, "rev-parse", f"{merged}^2") == sha
    assert "Auoreo-Task: kanban:default#1" in git(s.repo, "log", "-1", "--format=%B")
    got = await events(s.svc, "review.requested", "review.approved", "merge.completed")
    kinds = [t for t, _, _ in got]
    assert kinds == ["review.requested", "review.approved", "merge.completed"]
    assert got[0][1] == s.worker and got[2][2]["merge_sha"] == merged
    assert all(d.get("task_ref") == "kanban:default#1" for _, _, d in got)
    actions = await _audit(s.svc)
    assert "merge.approved" in actions and "merge.completed" in actions


async def test_always_is_not_remembered_and_review_is_no_grant(setup: Setup) -> None:
    s = setup
    sha = commit(s.tree.path, {"a.txt": "a\n"})
    _, err = await _approve(s, _args(s, sha), "always")
    assert not err
    sha2 = commit(s.tree.path, {"b.txt": "b\n"})
    task = asyncio.create_task(call(s.svc, _args(s, sha2)))
    pending = await card(s.svc)  # asked again: no grant was stored
    await s.svc.approvals.reply(pending.request_id, "reject")
    res = await task
    assert res.is_error and not (s.repo / "b.txt").exists()


# ------------------------------------------------------------------ nobody but the human
async def test_non_interactive_and_flag_off_and_worktree_session_are_refused(setup: Setup) -> None:
    s = setup
    sha = commit(s.tree.path, {"a.txt": "a\n"})
    res = await call(s.svc, _args(s, sha), interactive=False)
    assert res.is_error and "non-interactive" in res.content
    res = await call(s.svc, _args(s, sha), cwd=s.tree.path)  # a worker cannot merge its own branch
    assert res.is_error and "worktree" in res.content
    s.svc.settings.m7.orch.merge = False
    res = await call(s.svc, _args(s, sha))
    assert res.is_error and "m7.orch.merge" in res.content
    assert not (s.repo / "a.txt").exists() and s.svc.approvals.pending() == []


@pytest.mark.parametrize("mode", ["yolo", "acceptEdits", "rule"])
async def test_modes_and_rules_never_skip_the_card(
    setup: Setup, mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = setup
    monkeypatch.setattr(perm, "YOLO_FROZEN", True)
    rules = [Rule(permission="git_merge", pattern="*", action="allow")]
    engine = PermissionEngine(rules, mode="manual" if mode == "rule" else mode)  # type: ignore[arg-type]
    sha = commit(s.tree.path, {"a.txt": "a\n"})
    task = asyncio.create_task(call(s.svc, _args(s, sha), engine=engine))
    pending = await card(s.svc)
    await s.svc.approvals.reply(pending.request_id, "reject")
    assert (await task).is_error and not (s.repo / "a.txt").exists()


async def test_smart_mode_guardian_approval_still_asks_the_human(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auoreo.tools import executor

    async def approve(*a: object, **k: object) -> str:
        return "APPROVE"

    monkeypatch.setattr(executor, "guardian_verdict", approve)
    s = setup
    engine = PermissionEngine([Rule(permission="git_merge", pattern="*", action="ask")], mode="smart")
    sha = commit(s.tree.path, {"a.txt": "a\n"})
    task = asyncio.create_task(call(s.svc, _args(s, sha), engine=engine))
    pending = await card(s.svc)
    assert pending.tool == "git_merge" and pending.one_shot
    await s.svc.approvals.reply(pending.request_id, "reject")
    assert (await task).is_error and not (s.repo / "a.txt").exists()


async def test_a_granting_permission_hook_still_asks_the_human(setup: Setup) -> None:
    s = setup
    write(
        s.repo / ".auoreo" / "scripts" / "grant.py", "import json\nprint(json.dumps({'decision': 'allow'}))\n"
    )
    write(
        s.repo / ".auoreo" / "hooks" / "grant.yaml",
        "name: grant\ndescription: grants everything\nevent: PermissionRequest\nmatcher: 'git_merge'\n"
        f"command: ['{sys.executable}', './scripts/grant.py']\n",
    )
    s.svc.harness.reload()
    engine = PermissionEngine([Rule(permission="git_merge", pattern="*", action="ask")])
    sha = commit(s.tree.path, {"a.txt": "a\n"})
    task = asyncio.create_task(call(s.svc, _args(s, sha), engine=engine))
    pending = await card(s.svc)
    await s.svc.approvals.reply(pending.request_id, "reject")
    assert (await task).is_error and not (s.repo / "a.txt").exists()


async def test_cli_auto_never_answers_a_merge_card(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    from auoreo.cli import run_support

    s = setup
    sha = commit(s.tree.path, {"a.txt": "a\n"})
    task = asyncio.create_task(call(s.svc, _args(s, sha)))
    pending = await card(s.svc)
    data = {"request_id": pending.request_id, "tool": "git_merge", "permission": "git_merge"}
    answers = iter(["n"])
    monkeypatch.setattr(run_support, "_ask", lambda prompt: next(answers))
    await run_support._answer_permission(s.svc, data, auto=True)
    res = await task
    assert res.is_error and not (s.repo / "a.txt").exists()


async def test_reject_and_timeout_change_nothing(setup: Setup) -> None:
    s = setup
    head = _head(s)
    sha = commit(s.tree.path, {"a.txt": "a\n"})
    text, err = await _approve(s, _args(s, sha), "reject")
    assert err and _head(s) == head
    s.svc.approvals.timeout_s = 0.2
    res = await call(s.svc, _args(s, sha))
    assert res.is_error and "timed out" in res.content and _head(s) == head
    kinds = [t for t, _, _ in await events(s.svc, "review.rejected")]
    assert kinds == ["review.rejected", "review.rejected"]


# ------------------------------------------------------------------ the bound tuple
async def test_wrong_or_short_sha_is_refused_before_any_card(setup: Setup) -> None:
    s = setup
    sha = commit(s.tree.path, {"a.txt": "a\n"})
    res = await call(s.svc, _args(s, sha[:12]))  # the schema (and `pre_check`) refuse it
    assert res.is_error and "sha" in res.content and s.svc.approvals.pending() == []
    other = commit(s.tree.path, {"b.txt": "b\n"})
    res = await call(s.svc, _args(s, sha))  # the branch moved on to `other`
    assert res.is_error and sha[:12] in res.content and other[:12] in res.content
    res = await call(s.svc, {"branch": "main", "sha": sha})
    assert res.is_error and "branch" in res.content and s.svc.approvals.pending() == []
    denied = [d for t, _, d in await events(s.svc, "merge.denied")]
    assert denied[-1]["reason"] == "sha_mismatch"


async def test_sha_swapped_between_card_and_reply(setup: Setup) -> None:
    s = setup
    head = _head(s)
    sha = commit(s.tree.path, {"a.txt": "a\n"})
    task = asyncio.create_task(call(s.svc, _args(s, sha)))
    pending = await card(s.svc)
    commit(s.tree.path, {"evil.txt": "x\n"})  # a sibling moves the branch while the human reads
    await s.svc.approvals.reply(pending.request_id, "once")
    res = await task
    assert res.is_error and "moved" in res.content and _head(s) == head
    assert [d["reason"] for _, _, d in await events(s.svc, "merge.denied")] == ["moved_after_approval"]


async def test_target_head_moved_between_card_and_reply(setup: Setup) -> None:
    s = setup
    sha = commit(s.tree.path, {"a.txt": "a\n"})
    task = asyncio.create_task(call(s.svc, _args(s, sha)))
    pending = await card(s.svc)
    commit(s.repo, {"user.txt": "the user committed meanwhile\n"})
    moved = _head(s)
    await s.svc.approvals.reply(pending.request_id, "once")
    res = await task
    assert res.is_error and "moved" in res.content and _head(s) == moved


async def test_dirty_target_is_refused_before_and_after_the_card(setup: Setup) -> None:
    s = setup
    sha = commit(s.tree.path, {"a.txt": "a\n"})
    (s.repo / "README.md").write_text("local edit\n")
    res = await call(s.svc, _args(s, sha))
    assert res.is_error and "uncommitted" in res.content and s.svc.approvals.pending() == []
    git(s.repo, "checkout", "--quiet", "README.md")
    task = asyncio.create_task(call(s.svc, _args(s, sha)))
    pending = await card(s.svc)
    (s.repo / "README.md").write_text("edited while the card was open\n")
    await s.svc.approvals.reply(pending.request_id, "once")
    res = await task
    assert res.is_error and (s.repo / "README.md").read_text() == "edited while the card was open\n"


# ------------------------------------------------------------------ tampering
async def test_sibling_planted_commit_is_counted_on_the_card(services: Services, workdir: Path) -> None:
    s = await recorded_worktree(services, "kdefault-1")
    sib = await recorded_worktree(services, "kdefault-2", task_ref="kanban:default#2")
    planted = commit(sib.tree.path, {"planted.txt": "x\n"}, "planted")
    git(s.tree.path, "merge", "--quiet", "--ff-only", planted)  # the sibling's commit lands on s's branch
    sha = commit(s.tree.path, {"a.txt": "a\n"})
    task = asyncio.create_task(call(services, _args(s, sha)))
    pending = await card(services)
    assert "1 commit is also on auoreo/kdefault-2" in pending.detail and "2 commits" in pending.detail
    await services.approvals.reply(pending.request_id, "reject")
    await task


async def test_foreign_base_and_unrecorded_branch_are_refused(setup: Setup) -> None:
    s = setup
    git(s.repo, "checkout", "--quiet", "--orphan", "auoreo/foreign")
    git(s.repo, "rm", "-rq", "--cached", ".")
    commit(s.repo, {"x.txt": "unrelated history\n"})
    unrecorded = git(s.repo, "rev-parse", "HEAD")
    git(s.repo, "checkout", "--quiet", "-f", "main")
    (s.repo / "x.txt").unlink(missing_ok=True)
    res = await call(s.svc, {"branch": "auoreo/foreign", "sha": unrecorded})
    assert res.is_error and "no record" in res.content
    git(s.repo, "update-ref", f"refs/heads/{s.tree.branch}", unrecorded)  # a worker repoints the branch
    res = await call(s.svc, _args(s, unrecorded))
    assert res.is_error and "base" in res.content
    reasons = [d["reason"] for _, _, d in await events(s.svc, "merge.denied")]
    assert reasons == ["unrecorded", "foreign_base"]


async def test_definition_changes_are_refused_by_default_and_warned_on_request(setup: Setup) -> None:
    s = setup
    sha = commit(s.tree.path, {".auoreo/hooks/evil.yaml": "name: evil\n", "ok.txt": "ok\n"})
    res = await call(s.svc, _args(s, sha))
    assert res.is_error and ".auoreo/hooks/evil.yaml" in res.content and s.svc.approvals.pending() == []
    assert not (s.repo / ".auoreo" / "hooks" / "evil.yaml").exists()
    s.svc.settings.m7.orch.merge_definitions = "warn"
    task = asyncio.create_task(call(s.svc, _args(s, sha)))
    pending = await card(s.svc)
    assert "warn    changes .auoreo/hooks/evil.yaml" in pending.detail
    await s.svc.approvals.reply(pending.request_id, "reject")
    await task


@posix
async def test_worker_edits_to_its_git_dir_never_run(setup: Setup, tmp_path: Path) -> None:
    """The worker owns `<worktree>/.git` (and, without a sandbox, the shared hooks): an
    fsmonitor, a hook or a configured hooksPath it plants never runs during the merge."""
    s = setup
    sha = commit(s.tree.path, {"a.txt": "a\n"})
    marker = tmp_path / "WORKER_CODE_RAN"
    script = tmp_path / "evil.sh"
    script.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    script.chmod(0o755)
    pointer = s.tree.path / ".git"
    pointer.unlink()
    (pointer / "hooks").mkdir(parents=True)
    (pointer / "config").write_text(f"[core]\n\tfsmonitor = {script}\n\thooksPath = {tmp_path}\n")
    for hook in ("post-merge", "reference-transaction", "post-checkout"):
        (s.repo / ".git" / "hooks" / hook).write_text(f"#!/bin/sh\ntouch '{marker}'\n")
        (s.repo / ".git" / "hooks" / hook).chmod(0o755)
    text, err = await _approve(s, _args(s, sha), "once")
    assert not err, text
    assert (s.repo / "a.txt").is_file() and not marker.exists()


# ------------------------------------------------------------------ conflicts
async def test_conflict_opens_one_blocked_task_and_touches_nothing(setup: Setup) -> None:
    s = setup
    teams = s.svc.ext["teams"]
    origin = await teams.kanban.create("default", "the original task")
    sha = commit(s.tree.path, {"README.md": "theirs\n"})
    commit(s.repo, {"README.md": "ours\n"})
    head, before = _head(s), git(s.repo, "status", "--porcelain")
    res = await call(s.svc, _args(s, sha))
    assert res.is_error and "conflict" in res.content and "README.md" in res.content
    assert s.svc.approvals.pending() == []
    assert _head(s) == head and git(s.repo, "status", "--porcelain") == before
    got = [d for _, _, d in await events(s.svc, "merge.conflicts")]
    assert got[0]["files"] == ["README.md"] and got[0]["task_ref"] == "kanban:default#1"
    task_id = got[0]["conflict_task"]
    row = await teams.kanban.get(task_id)
    assert row.status == "blocked" and row.title.startswith(
        f"Resolve merge conflicts: {s.tree.branch} onto main"
    )
    shown = await teams.kanban.show(task_id)
    assert any(a["ref"] == str(s.tree.path) for a in shown["attachments"])
    assert any(link["kind"] == "relates" and link["task_id"] == origin.id for link in shown["links"])
    res = await call(s.svc, _args(s, sha))  # same sha and target HEAD: the same task
    again = [d for _, _, d in await events(s.svc, "merge.conflicts")]
    assert again[-1]["conflict_task"] == task_id
    assert len(await teams.kanban.list_tasks("default")) == 2
