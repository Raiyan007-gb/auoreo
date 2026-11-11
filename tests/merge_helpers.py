"""Test helpers for M7 W4 Wave 2 (`git_merge`): a server checkout with one recorded worker
worktree, a commit in it, and a tool call through the executor that answers the card."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from auoreo.core.approvals import Pending
from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.runtime.worktrees import NewFeature
from auoreo.teams import worktree_spawn
from auoreo.tools.context import ToolContext, ToolResult
from tests.worktree_helpers import flags_on, git, git_init

ALLOW = [Rule(permission="*", pattern="*", action="allow")]


@dataclass
class Setup:
    svc: Services
    repo: Path
    tree: NewFeature
    worker: str  # the worker session id (aggregate of worktree.created)


def identity(repo: Path) -> None:
    git(repo, "config", "user.name", "t")
    git(repo, "config", "user.email", "t@example.invalid")


async def recorded_worktree(
    svc: Services, slug: str = "kdefault-1", *, task_ref: str | None = "kanban:default#1"
) -> Setup:
    """`svc.cwd` becomes a clean checkout; a worker worktree is created and recorded the way
    the dispatcher does (`worktree.created` on the worker session)."""
    repo = svc.cwd
    if not (repo / ".git").exists():
        git_init(repo)
        identity(repo)
    flags_on(svc)
    svc.settings.m7.orch.merge = True
    tree = await worktree_spawn.prepare(svc, slug)
    row = await svc.create_session(cwd=tree.path)
    await svc.emit(row.id, "worktree.created", worktree_spawn.payload(tree, task_ref=task_ref))
    return Setup(svc, repo, tree, row.id)


def commit(tree: Path, files: dict[str, str], msg: str = "work") -> str:
    for path, body in files.items():
        target = tree / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        git(tree, "add", path)
    git(tree, "commit", "--quiet", "-m", msg)
    return git(tree, "rev-parse", "HEAD")


def ctx_for(svc: Services, cwd: Path, *, interactive: bool = True, session_id: str = "ses_m") -> ToolContext:
    return ToolContext(
        session_id=session_id,
        cwd=cwd,
        home=svc.settings.home,
        harness=svc.harness,
        interactive=interactive,
        services={"services": svc},
    )


async def call(
    svc: Services,
    args: dict[str, Any],
    *,
    cwd: Path | None = None,
    engine: PermissionEngine | None = None,
    interactive: bool = True,
) -> ToolResult:
    engine = engine or PermissionEngine(list(ALLOW))
    ctx = ctx_for(svc, cwd or svc.cwd, interactive=interactive)
    ctx.services["engine"] = engine
    out = await svc.coordinator.runner.executor.run(
        ToolCall("c1", "git_merge", json.dumps(args)), tools=svc.harness.tools, ctx=ctx, engine=engine
    )
    return out.result


async def card(svc: Services, *, timeout: float = 10.0) -> Pending:
    for _ in range(int(timeout / 0.02)):
        pending = [p for p in svc.approvals.pending() if p.tool == "git_merge"]
        if pending:
            # the handler's own card, never the executor's JSON-arguments prompt
            assert pending[0].detail.startswith("merge   ") and pending[0].one_shot, pending[0].detail
            return pending[0]
        await asyncio.sleep(0.02)
    raise AssertionError("no git_merge approval card appeared")


async def events(svc: Services, *types: str) -> list[tuple[str, str, dict[str, Any]]]:
    rows = await svc.events.replay(0, None, limit=100_000)
    return [(e.type, e.aggregate_id, e.data) for e in rows if not types or e.type in types]


def init_with_identity(repo: Path) -> Path:
    git_init(repo)
    identity(repo)
    return repo
