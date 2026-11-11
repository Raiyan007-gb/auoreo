"""`subtask: true` slash commands (G6, spec §2.11 `SlashDef{name:"review", subtask:true}`).

The rendered body runs as a **background child session** (teams `spawn`, agent from the
command's `agent:`), so the parent's context stays clean; the child's result comes back as a
task-notification. With `worktree: true` the child works in an isolated git worktree:
`git worktree add --detach <cwd>/.auoreo/worktrees/<slug> HEAD`, then the parent checkout's tracked changes
(`git diff --binary HEAD`) are applied there, so tests and edits never touch the user's
tree. `{{worktree}}` in the body is that path (or the cwd without a worktree). Untracked
files are not copied. Worktrees stay until removed (`git worktree remove <path>`).

Without the teams extension, in a non-interactive run, or when spawning is refused, the body
is expanded inline.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from auoreo.core.secrets import child_env
from auoreo.harness.orchestrator import render

if TYPE_CHECKING:
    from auoreo.runtime.commands import Expanded, Invocation

GIT_TIMEOUT_S = 60.0


class WorktreeError(RuntimeError):
    pass


NO_HOOKS = ("-c", f"core.hooksPath={os.devnull}")
"""Every git call the harness makes runs no hook (M7 W4 Wave 2): a `post-checkout`,
`reference-transaction` or `post-merge` in the shared hooks directory (or a configured
`core.hooksPath`) would otherwise run as the server user, outside every sandbox, and a worker
without an enforced sandbox can write those. `os.devnull` names no directory, so no file under
it can exist; `-c` wins over every config file."""


def _verb(args: tuple[str, ...]) -> str:
    """The git subcommand in `args` (after any leading `-c key=value` pairs)."""
    rest = list(args)
    while len(rest) >= 2 and rest[0] == "-c":
        rest = rest[2:]
    return rest[0] if rest else "git"


async def _git_rc(
    *args: str, cwd: Path, stdin: bytes | None = None, env: dict[str, str] | None = None
) -> tuple[int, bytes, bytes]:
    """`git <args>` with hooks disabled, a scrubbed environment and a timeout: `(code, out, err)`."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *NO_HOOKS,
        *args,
        cwd=cwd,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**child_env([], None), **(env or {})},
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), GIT_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise WorktreeError(f"git {_verb(args)} timed out") from None
    return proc.returncode or 0, out, err


async def _git(*args: str, cwd: Path, stdin: bytes | None = None, env: dict[str, str] | None = None) -> bytes:
    code, out, err = await _git_rc(*args, cwd=cwd, stdin=stdin, env=env)
    if code != 0:
        raise WorktreeError(f"git {_verb(args)} failed: {err.decode(errors='replace').strip()[-300:]}")
    return out


def _prepare_root(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    ignore = root / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")


async def create_worktree(repo: Path, root: Path, slug: str) -> Path:
    """Detached worktree of `repo`'s HEAD under `root/slug` with the working changes applied."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", slug):
        raise WorktreeError(f"invalid worktree slug '{slug}'")
    try:
        top = Path((await _git("rev-parse", "--show-toplevel", cwd=repo)).decode().strip())
    except (WorktreeError, OSError) as exc:
        raise WorktreeError(f"{repo} is not inside a git repository ({exc})") from None
    dest = root / slug
    _prepare_root(root)
    await _git("worktree", "add", "--detach", "--quiet", str(dest), "HEAD", cwd=top)
    diff = await _git("diff", "--binary", "HEAD", cwd=top)
    if diff.strip():
        await _git("apply", "--whitespace=nowarn", "-", cwd=dest, stdin=diff)
    return dest


async def remove_worktree(path: Path) -> None:
    await _git("worktree", "remove", "--force", str(path), cwd=path.parent)


def worktree_root(settings: object, cwd: Path) -> Path:
    """`settings.g6.review.worktree_root`, else `<cwd>/.auoreo/worktrees` (spec: inside the
    project, so tool path checks treat it as the workspace; a `*` .gitignore hides it)."""
    g6 = getattr(settings, "g6", None)
    raw = g6.review.worktree_root if g6 is not None else None
    return Path(raw).expanduser() if raw else cwd / ".auoreo" / "worktrees"


async def run_subtask(inv: Invocation) -> Expanded:
    from auoreo.runtime.commands import Expanded

    s, rt, m = inv.coord.s, inv.rt, inv.command.manifest
    tag = uuid.uuid4().hex[:8]
    where: Path = s.cwd
    worktree: Path | None = None
    if m.worktree:
        try:
            worktree = await create_worktree(s.cwd, worktree_root(s.settings, s.cwd), f"{m.name}-{tag}")
            where = worktree
        except (WorktreeError, OSError) as exc:
            await s.emit(
                rt.session_id,
                "command.subtask_failed",
                {"command": m.name, "stage": "worktree", "error": str(exc)},
            )
    text = render(
        inv.command.template or "{{args}}", {"args": inv.args, "input": inv.text, "worktree": str(where)}
    )
    text = text.strip() or inv.text
    agent = m.agent or inv.agent
    teams = s.ext.get("teams")
    # A non-interactive run (CLI `--print`, jobs, webhooks) needs the result in this turn, so
    # the body runs inline there (still in the worktree) instead of as a background child.
    if teams is None or not rt.interactive:
        return Expanded(text, agent)
    try:
        member = await teams.spawn(
            rt.session_id,
            agent=agent or s.settings.agent,
            prompt=text,
            name=f"{m.name}_{tag}",
            background=True,
            cwd=worktree if s.settings.m7.orch.session_cwd else None,  # M7 W4: tools run there
        )
    except ValueError as exc:  # TeamsError: depth limit, unknown agent, …
        await s.emit(
            rt.session_id, "command.subtask_failed", {"command": m.name, "stage": "spawn", "error": str(exc)}
        )
        return Expanded(text, agent)
    await s.emit(
        rt.session_id,
        "command.subtask",
        {
            "command": m.name,
            "task_id": member.task_id,
            "name": f"{m.name}_{tag}",
            "agent": member.agent,
            "worktree": str(worktree) if worktree else None,
        },
    )
    return Expanded(text, agent, handled=True)
