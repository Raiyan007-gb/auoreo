"""M7 W4 hardening: `apply_patch` never runs a formatter found inside a worktree (the worker
controls `node_modules/.bin/prettier`); the server cwd (flag off) keeps formatting."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from auoreo.core.services import Services
from auoreo.runtime import cwd as session_cwd
from auoreo.runtime import worktrees as wt
from tests.teams_helpers import call_tool, tool_ctx
from tests.worktree_helpers import flags_on, git_init

PATCH = "*** Begin Patch\n*** Add File: a.js\n+let  x=1\n*** End Patch\n"


def _plant(root: Path, marker: Path) -> None:
    exe = root / "node_modules" / ".bin" / "prettier"
    exe.parent.mkdir(parents=True)
    exe.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)


@pytest.mark.skipif(sys.platform == "win32", reason="posix script")
async def test_worktree_prettier_is_not_executed(services: Services, workdir: Path, tmp_path: Path) -> None:
    git_init(workdir)
    flags_on(services)
    tree = await wt.task_worktree(workdir, session_cwd.orch_root(services.settings, services.cwd), "fmt")
    marker = tmp_path / "marker"
    _plant(tree.path, marker)
    row = await services.create_session(title="w", cwd=tree.path)
    ctx = tool_ctx(services, row.id)
    ctx.cwd = tree.path
    out, err = await call_tool(services, ctx, "apply_patch", {"patch": PATCH})
    assert not err and (tree.path / "a.js").is_file()
    assert not marker.exists()
    assert "format step skipped in a worktree session" in out


@pytest.mark.skipif(sys.platform == "win32", reason="posix script")
async def test_server_cwd_still_formats(services: Services, workdir: Path, tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    _plant(workdir, marker)
    row = await services.create_session(title="s")
    out, err = await call_tool(services, tool_ctx(services, row.id), "apply_patch", {"patch": PATCH})
    assert not err and marker.exists() and "skipped in a worktree" not in out
