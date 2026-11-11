"""M7 W4 hardening: what a worker in its own worktree must not be able to reach.

Each test reproduces a proven escape (a patch outside the worktree, git run by the
orchestrator inside the worker-controlled folder, a planted branch, gc removing a live child,
hooks running the worker's files) and asserts it is closed for worktree sessions."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.gateway.fake import Scripted
from auoreo.harness.manifests import Rule
from auoreo.runtime import cwd as session_cwd
from auoreo.runtime import worktrees as wt
from tests.teams_helpers import RoutedGateway, call_tool, tool_ctx, turn
from tests.worktree_helpers import flags_on, git_init


@pytest.fixture
def gateway() -> RoutedGateway:
    return RoutedGateway()


@pytest.fixture(autouse=True)
def _tmp_is_not_a_free_zone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The default rules allow `$TMP/*` as an external directory; pytest's checkouts live
    there, so point `$TMP` elsewhere to see the real outside-cwd decision."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))


async def _tree(svc: Services, slug: str = "job-1") -> wt.NewFeature:
    root = session_cwd.orch_root(svc.settings, svc.cwd)
    return await wt.task_worktree(svc.cwd, root, slug)


def _allow(rt: Any, *permissions: str) -> None:
    rules = [Rule(permission=p, pattern="*", action="allow") for p in permissions]
    rt.engine = PermissionEngine([], session_rules=rules)


def _patches(workdir: Path) -> dict[str, str]:
    (workdir / "old.txt").write_text("a\n", encoding="utf-8")
    return {
        "add": f"*** Begin Patch\n*** Add File: {workdir / 'added.txt'}\n+x\n*** End Patch\n",
        "update": f"*** Begin Patch\n*** Update File: {workdir / 'old.txt'}\n@@\n-a\n+b\n*** End Patch\n",
        "move": (
            "*** Begin Patch\n*** Update File: mine.txt\n*** Move to: "
            f"{workdir / 'moved.txt'}\n@@\n-m\n+n\n*** End Patch\n"
        ),
    }


def _untouched(workdir: Path) -> None:
    assert not (workdir / "added.txt").exists() and not (workdir / "moved.txt").exists()
    assert (workdir / "old.txt").read_text(encoding="utf-8") == "a\n"


# ------------------------------------------------------------------ 1: apply_patch
async def test_apply_patch_outside_the_worktree_is_refused_non_interactively(
    services: Services, workdir: Path, gateway: RoutedGateway
) -> None:
    git_init(workdir)
    flags_on(services)
    tree = (await _tree(services)).path
    (tree / "mine.txt").write_text("m\n", encoding="utf-8")
    patches = _patches(workdir)
    row = await services.create_session(cwd=tree)
    _allow(await services.coordinator.runtime(row.id), "edit", "read")
    gateway.route(
        "PATCH",
        *(Scripted(tool_calls=[("apply_patch", {"patch": p})]) for p in patches.values()),
        Scripted(text="done"),
    )
    # three refusals in a row may trip the denial guard: the run then ends "blocked"
    assert (await turn(services, row.id, "PATCH the parent")).status in {"completed", "blocked"}
    events = await services.events.replay(0, frozenset({f"session:{row.id}"}), limit=100_000)
    done = [e.data for e in events if e.type == "tool.completed"]
    assert [d["is_error"] for d in done] == [True, True, True]
    _untouched(workdir)
    assert (tree / "mine.txt").read_text(encoding="utf-8") == "m\n"


async def test_apply_patch_outside_the_worktree_asks_interactively(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git_init(workdir)
    flags_on(services)
    tree = (await _tree(services)).path
    (tree / "mine.txt").write_text("m\n", encoding="utf-8")
    asked: list[tuple[str, str]] = []
    executor = services.coordinator.runner.executor
    real_ask = executor._ask

    async def ask(
        call: Any, tool: Any, args: Any, ctx: Any, engine: Any, perm: str, pattern: str, *a: Any
    ) -> Any:
        if perm != "external_directory":
            return await real_ask(call, tool, args, ctx, engine, perm, pattern, *a)
        asked.append((perm, pattern))
        return executor._err(call, "refused by the test"), None

    monkeypatch.setattr(executor, "_ask", ask)
    row = await services.create_session(cwd=tree)
    ctx = tool_ctx(services, row.id)
    ctx.cwd, ctx.interactive = tree, True
    ctx.services["engine"] = PermissionEngine([Rule(permission="edit", pattern="*", action="allow")])
    for kind, patch in _patches(workdir).items():
        asked.clear()
        _, is_error = await call_tool(services, ctx, "apply_patch", {"patch": patch})
        assert is_error and asked == [("external_directory", f"{workdir}/*")], kind
    _untouched(workdir)
    # inside the worktree nothing is asked
    inside = "*** Begin Patch\n*** Add File: ok.txt\n+y\n*** End Patch\n"
    asked.clear()
    assert (await call_tool(services, ctx, "apply_patch", {"patch": inside}))[1] is False
    assert asked == [] and (tree / "ok.txt").exists()


async def test_apply_patch_in_the_server_cwd_is_unchanged(services: Services, workdir: Path) -> None:
    """Not a worktree session: the patch is gated by `edit` only, as before."""
    git_init(workdir)
    outside = workdir.parent / "elsewhere"
    outside.mkdir()
    row = await services.create_session()
    ctx = tool_ctx(services, row.id)
    patch = f"*** Begin Patch\n*** Add File: {outside / 'n.txt'}\n+x\n*** End Patch\n"
    assert (await call_tool(services, ctx, "apply_patch", {"patch": patch}))[1] is False
    assert (outside / "n.txt").exists()
