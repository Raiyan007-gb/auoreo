"""M7 W4 hardening, part 2: hooks never run in a worker's worktree, and the smaller fixes
(flag-off notice for a stored worktree cwd, a NUL in a stored cwd, rollback when a spawn
fails or is cancelled, a relative `gitdir` record)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from auoreo.core.services import Services
from auoreo.runtime import cwd as session_cwd
from auoreo.runtime import worktrees as wt
from auoreo.runtime.hooks import HookRunner
from tests.conftest import write
from tests.worktree_helpers import flags_on, git_init


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
