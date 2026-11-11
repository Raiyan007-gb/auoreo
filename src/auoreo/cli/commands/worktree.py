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


async def _plan(days: float, force: bool, protected: set[Path] | None = None) -> tuple[Path, list[wt.GcItem]]:
    st = settings()
    if not st.m7.orch.worktrees:
        return await wt.plan_gc(Path.cwd(), _root(), days, force=force)
    from auoreo.runtime.cwd import orch_root
    from auoreo.teams.worktree_gc import guard_from_settings

    g = await guard_from_settings(st, _note)
    if protected is not None:
        protected |= g.protected
    top, items = await wt.plan_gc(
        Path.cwd(), _root(), days, force=force, protected=g.protected, activity=g.activity
    )
    extra = orch_root(st, top)
    if extra != (_root() or top / ".auoreo" / "worktrees"):
        more = await wt.plan_gc(
            Path.cwd(), extra, days, force=force, protected=g.protected, activity=g.activity
        )
        items += more[1]
    return top, items


def _note(text: str) -> None:
    err_console.print(f"[yellow]note:[/yellow] {escape(text)}")


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
    reconcile: Annotated[
        bool,
        typer.Option(
            "--reconcile",
            help="M7: also delete merged auoreo/* branches, report drift against kanban attachments, "
            "and journal removals for the server's worktree.removed/gc events.",
        ),
    ] = False,
    restore: Annotated[
        bool,
        typer.Option("--restore", help="With --reconcile: recreate a missing branch at its recorded tip."),
    ] = False,
) -> None:
    """Remove stale `.auoreo/worktrees/*`: merged, untouched, missing or orphaned (dirty ones are kept)."""
    days = settings().cli2.worktree_stale_days if older_than is None else older_than
    if reconcile or restore:
        _reconcile(days, dry_run=dry_run, yes=yes, force=force, as_json=as_json, restore=restore)
        return
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


def _reconcile(days: float, *, dry_run: bool, yes: bool, force: bool, as_json: bool, restore: bool) -> None:
    """`gc --reconcile` (M7 W4 Wave 2, `teams/worktree_reconcile.py`): writes no event; a running
    server turns the journal under `<home>/worktrees/` into `worktree.removed|gc` events."""
    from auoreo.teams import worktree_reconcile as rc

    st = settings()
    if not st.m7.orch.worktrees:
        err_console.print("[red]--reconcile needs m7.orch.worktrees[/red]")
        raise typer.Exit(1)

    async def plan() -> tuple[Path, list[wt.GcItem], list[rc.Finding], rc.Records]:
        protected: set[Path] = set()
        top, items = await _plan(days, force, protected)
        rec = await rc.records_from_settings(st, _note)
        return top, items, await rc.plan(top, items, rec, protected, restore=restore), rec

    try:
        top, items, findings, rec = asyncio.run(plan())
    except WorktreeError as exc:
        err_console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    todo = [i for i in items if i.action != "keep"]
    acts = [f for f in findings if f.action in ("delete_branch", "restore")]
    if as_json:
        rows = [{"path": str(i.path), "reason": i.reason, "action": i.action} for i in items]
        dump_json({"worktrees": rows, "findings": [rc.finding_dict(f) for f in findings]})
    else:
        for i in items:
            console.print(f"{i.action:6} {i.path}  ({escape(i.reason)})")
        for f in findings:
            console.print(f"{f.action:6} {escape(f.branch or f.path or '')}  ({escape(f.reason)})")
        if not items and not findings:
            console.print("nothing to reconcile")
    if dry_run or not (todo or acts):
        return
    if not yes:
        typer.confirm(f"Remove/prune {len(todo)} worktree(s) and change {len(acts)} branch(es)?", abort=True)
    try:
        asyncio.run(rc.apply(top, items, findings, st.home, refs=rec.refs, force=force))
    except WorktreeError as exc:
        err_console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    if not as_json:
        console.print(f"reconciled: {len(todo)} worktree(s), {len(acts)} branch change(s); journal written")
