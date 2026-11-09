"""H4: `auoreo new-feature <slug>` and `auoreo worktree gc` (spec §25.15; logic in
`runtime/worktrees.py`). Worktrees live under `<repo>/.auoreo/worktrees/` (or
`g6.review.worktree_root`), hidden from git by a `*` .gitignore. With `m7.orch.worktrees`, gc also
covers the orchestrator root (`runtime/cwd.orch_root`) and keeps the worktrees of claimed kanban
tasks; it prints its plan and emits no event."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape

from auoreo.cli.common import console, dump_json, err_console, settings
from auoreo.runtime import worktrees as wt
from auoreo.runtime.subtask import WorktreeError

app = typer.Typer(no_args_is_help=True)


def _root() -> Path | None:
    raw = settings().g6.review.worktree_root
    return Path(raw).expanduser() if raw else None


async def _plan(days: float, force: bool) -> tuple[Path, list[wt.GcItem]]:
    st = settings()
    if not st.m7.orch.worktrees:
        return await wt.plan_gc(Path.cwd(), _root(), days, force=force)
    from auoreo.runtime.cwd import orch_root

    keep = await _protected()
    top, items = await wt.plan_gc(Path.cwd(), _root(), days, force=force, protected=keep)
    extra = orch_root(st, top)
    if extra != (_root() or top / ".auoreo" / "worktrees"):
        items += (await wt.plan_gc(Path.cwd(), extra, days, force=force, protected=keep))[1]
    return top, items


async def _protected() -> set[Path]:
    """Worktrees of claimed kanban tasks; empty (with a note) when the store cannot be read."""
    from auoreo.core.db import Database
    from auoreo.teams.worktree_spawn import protected_paths

    st = settings()
    if not _exists(st.db_path):
        return set()
    try:
        db = Database(st.db_path, encryption=st.db_encryption)
        await db.init()
        try:
            return await protected_paths(db)
        finally:
            await db.close()
    except Exception as exc:  # gc still works without the task store
        err_console.print(f"[yellow]note:[/yellow] kanban store not readable ({escape(str(exc))})")
        return set()


def _exists(path: Path) -> bool:
    return path.exists()


def new_feature(
    slug: Annotated[str, typer.Argument(help="e.g. fix-login or auth/totp (branch auoreo/<slug>).")],
    base: Annotated[
        str | None, typer.Option("--base", help="Start point (default origin/HEAD, else HEAD).")
    ] = None,
    no_fetch: Annotated[bool, typer.Option("--no-fetch", help="Do not `git fetch` first.")] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Continue even if an open PR uses the branch.")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create (or resume) an isolated worktree + branch for one task; never build on main."""
    try:
        out = asyncio.run(
            wt.new_feature(Path.cwd(), slug, _root(), base=base, fetch=not no_fetch, force=force)
        )
    except WorktreeError as exc:
        err_console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    if as_json:
        dump_json(
            {
                "path": str(out.path),
                "branch": out.branch,
                "base": out.base,
                "resumed": out.resumed,
                "notes": out.notes,
            }
        )
        return
    verb = "resumed" if out.resumed else f"created from {out.base}"
    console.print(f"{out.branch} {verb}: {out.path}")
    for note in out.notes:
        err_console.print(f"[yellow]note:[/yellow] {escape(note)}")
    print(f"cd {out.path}")


@app.command("gc")
def gc(
    older_than: Annotated[
        float | None, typer.Option("--older-than", help="Days untouched (default cli2.worktree_stale_days).")
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Only show what would be removed.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Also remove worktrees with uncommitted changes.")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Remove stale `.auoreo/worktrees/*`: merged, untouched, missing or orphaned (dirty ones are kept)."""
    days = settings().cli2.worktree_stale_days if older_than is None else older_than
    try:
        top, items = asyncio.run(_plan(days, force))
    except WorktreeError as exc:
        err_console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    todo = [i for i in items if i.action != "keep"]
    if as_json:
        dump_json([{"path": str(i.path), "reason": i.reason, "action": i.action} for i in items])
    else:
        for i in items:
            console.print(f"{i.action:6} {i.path}  ({escape(i.reason)})")
        if not items:
            console.print("no worktrees under the worktree root")
    if dry_run or not todo:
        return
    if not yes:
        typer.confirm(f"Remove/prune {len(todo)} worktree(s)?", abort=True)
    try:
        asyncio.run(wt.apply_gc(top, todo, registered_force=force))
    except WorktreeError as exc:
        err_console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    if not as_json:
        console.print(f"removed {len(todo)} worktree(s)")
