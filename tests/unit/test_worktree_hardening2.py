"""M7 W4 hardening, part 2: hooks never run in a worker's worktree, and the smaller fixes
(flag-off notice for a stored worktree cwd, a NUL in a stored cwd, rollback when a spawn
fails or is cancelled, a relative `gitdir` record)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from auoreo.core.services import Services
from auoreo.runtime import cwd as session_cwd
from auoreo.runtime import worktrees as wt
from auoreo.runtime.hooks import HookRunner
from tests.conftest import write
from tests.worktree_helpers import flags_on, git_init, worktree_paths


async def _tree(svc: Services, slug: str = "job-1") -> wt.NewFeature:
    root = session_cwd.orch_root(svc.settings, svc.cwd)
    return await wt.task_worktree(svc.cwd, root, slug)


def _same(a: Path | str, b: Path | str) -> bool:
    return Path(a).resolve() == Path(b).resolve()


def _hook(workdir: Path, name: str, command: list[str]) -> None:
    body = json.dumps({"name": name, "description": "probe", "event": "PreToolUse", "command": command})
    write(workdir / ".auoreo" / "hooks" / f"{name}.yaml", body + "\n")


# ------------------------------------------------------------------ 7: hooks
@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell script")
async def test_hooks_run_in_the_server_cwd_and_get_the_worktree_as_data(
    services: Services, workdir: Path, tmp_path: Path
) -> None:
    git_init(workdir)
    flags_on(services)
    tree = (await _tree(services)).path
    out = tmp_path / "probe.json"
    probe = (
        "import json, os, sys; d = json.load(sys.stdin); "
        f"open({str(out)!r}, 'w').write(json.dumps({{'cwd': os.getcwd(), 'payload': d.get('cwd')}}))"
    )
    _hook(workdir, "probe", [sys.executable, "-c", probe])
    marker = tmp_path / "WORKER_CODE_RAN"
    script = tree / "check.sh"  # a worker-written file a bare relative command would pick up
    script.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    script.chmod(0o755)
    _hook(workdir, "relative", ["./check.sh"])
    services.harness.reload()
    row = await services.create_session(cwd=tree)
    await services.coordinator.runtime(row.id)
    runner = HookRunner(services)
    await runner.run("PreToolUse", session_id=row.id, payload={"tool": "x"}, match_key="x")
    seen = json.loads(out.read_text(encoding="utf-8"))
    assert _same(seen["cwd"], workdir)
    assert seen["payload"] == str(tree)
    assert not marker.exists()
    # the server cwd's own sessions: same process cwd, no extra payload key
    plain = await services.create_session()
    await runner.run("PreToolUse", session_id=plain.id, payload={"tool": "x"}, match_key="x")
    seen = json.loads(out.read_text(encoding="utf-8"))
    assert _same(seen["cwd"], workdir) and seen["payload"] is None


# ------------------------------------------------------------------ 8a/8b: stored cwd
async def test_flag_off_a_stored_worktree_cwd_is_audited_not_silent(
    services: Services, workdir: Path
) -> None:
    from sqlalchemy import select

    from auoreo.core.db import AuditRow

    git_init(workdir)
    flags_on(services)
    tree = (await _tree(services)).path
    row = await services.create_session(cwd=tree)
    plain = await services.create_session()
    services.settings.m7.orch.session_cwd = False  # rollback: the stored cwd is not honoured
    services.settings.m7.orch.worktrees = False
    rt = await services.coordinator.runtime(row.id)
    await services.coordinator.runtime(plain.id)
    assert rt.cwd is None and rt.cwd_error is None  # it runs in the server cwd, as before
    async with services.db.session() as s:
        q = select(AuditRow).where(AuditRow.action == "session.cwd_ignored")
        rows = list((await s.execute(q)).scalars())
    assert [r.session_id for r in rows] == [row.id]  # once, and only for the worktree session


async def test_a_nul_in_the_stored_cwd_fails_closed(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git_init(workdir)
    flags_on(services)
    row = await services.create_session()

    async def stored(_svc: Any, _sid: str) -> str:
        return f"{workdir}/bad\x00path"

    monkeypatch.setattr(session_cwd, "stored_cwd", stored)
    rt = await services.coordinator.runtime(row.id)
    assert rt.cwd is None and rt.cwd_error and session_cwd.MISSING in rt.cwd_error


# ------------------------------------------------------------------ 8c/8d: rollback
async def test_the_dispatcher_releases_the_task_when_the_session_cwd_is_refused(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auoreo.teams.dispatcher import KanbanDispatcher

    git_init(workdir)
    flags_on(services)
    cfg = services.settings.i3.kanban
    cfg.boards, cfg.agent = ["jobs"], "implementer"
    kb = services.ext["teams"].kanban
    task = await kb.create("jobs", "KREFUSE do it")

    async def refuse(**_: Any) -> Any:
        raise session_cwd.SessionCwdError(f"{session_cwd.MISSING}: test")

    monkeypatch.setattr(services, "create_session", refuse)
    disp = KanbanDispatcher(services)
    assert await disp.tick() == []
    row = await kb.get(task.id)
    assert (row.status, row.worker) == ("ready", None) and disp.guarded(task.id) == "worktree failed"
    assert len(worktree_paths(workdir)) == 1  # the worktree made for it is gone


def _delegate_module(services: Services) -> dict[str, Any]:
    return services.harness.tools["delegate_task"].handler().__globals__  # type: ignore[no-any-return,attr-defined]


async def test_a_cancelled_delegate_spawn_removes_its_worktree(services: Services, workdir: Path) -> None:
    git_init(workdir)
    flags_on(services)
    mod = _delegate_module(services)
    tree = await _tree(services, "cancel-me")
    ctx = SimpleNamespace(services={"services": services}, session_id="ses_x")

    class Teams:
        async def spawn(self, *_: Any, **__: Any) -> Any:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await mod["_spawn"](Teams(), ctx, {"goal": "x"}, "n", False, {}, tree)
    assert len(worktree_paths(workdir)) == 1


async def test_a_cancelled_batch_removes_the_worktrees_it_made(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git_init(workdir)
    flags_on(services)
    mod = _delegate_module(services)
    real = mod["worktree_spawn"].prepare
    calls: list[str] = []

    async def prepare(svc: Services, slug: str, **kw: Any) -> Any:
        calls.append(slug)
        if len(calls) == 2:
            raise asyncio.CancelledError
        return await real(svc, slug, **kw)

    monkeypatch.setattr(mod["worktree_spawn"], "prepare", prepare)
    ctx = SimpleNamespace(services={"services": services})
    tasks = [{"goal": "a", "worktree": True}, {"goal": "b", "worktree": True}]
    with pytest.raises(asyncio.CancelledError):
        await mod["_batch_trees"](ctx, tasks, ["a", "b"], {})
    assert len(calls) == 2 and len(worktree_paths(workdir)) == 1


# ------------------------------------------------------------------ 8e: relative gitdir record
def _record(admin: Path, pointer: Path) -> None:
    """Write the admin `gitdir` record relative to its own folder, as `useRelativePaths` does."""
    (admin / "gitdir").write_text(os.path.relpath(pointer, admin) + "\n", encoding="utf-8")


async def test_a_relative_gitdir_record_is_read_against_its_own_folder(
    services: Services, workdir: Path
) -> None:
    """Regression for the relative-record fix (`worktree.useRelativePaths`)."""
    git_init(workdir)
    tree = (await _tree(services, "job")).path
    admin = workdir / ".git" / "worktrees" / "job"
    _record(admin, tree / ".git")
    allowed = {p.resolve() for p in await session_cwd._git_paths(tree, workdir)}
    assert admin.resolve() in allowed
    _record(admin, tree.parent / "nonexistent" / ".git")
    assert await session_cwd._git_paths(tree, workdir) == []
