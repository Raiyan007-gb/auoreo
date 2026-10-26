"""`auoreo skills search|install|approve|remove|list|verify|taps` — the skills hub (G6).

Installs land in quarantine (invisible to agents) until `approve`; everything is pinned by
sha256 in `~/.auoreo/skills.lock`. `auoreo skill lint` (singular) stays the linter.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from auoreo.cli.common import console, err_console, settings, with_services
from auoreo.core.settings import write_settings_value
from auoreo.skillhub import HubError, SkillsHub, mcp_skills

app = typer.Typer(no_args_is_help=True)
taps_app = typer.Typer(no_args_is_help=False, invoke_without_command=True)
app.add_typer(taps_app, name="taps", help="List, add or remove hub taps (HTTP index or git URL).")


def _hub() -> SkillsHub:
    st = settings()
    return SkillsHub(st.home, st.g6.skills)


def _fail(exc: Exception) -> typer.Exit:
    err_console.print(f"[red]✗[/red] {exc}")
    return typer.Exit(1)


@app.command("search")
def search(query: Annotated[str, typer.Argument(help="Substring of name or description.")] = "") -> None:
    """Search every configured tap."""
    hub = _hub()
    if not hub.cfg.taps:
        err_console.print("no taps configured; add one with `auoreo skills taps add <url>`")
        raise typer.Exit(1)
    hits, errors = asyncio.run(hub.search(query))
    for e in errors:
        err_console.print(f"[yellow]![/yellow] {e}")
    installed = hub.lock()
    for s in hits:
        mark = f" [{installed[s.name]['status']}]" if s.name in installed else ""
        console.print(f"{s.name}{mark} — {s.description}  [dim]{s.tap}[/dim]")
    if not hits:
        console.print("no matching skills")


@app.command("install")
def install(
    name: str,
    tap: Annotated[str | None, typer.Option(help="Only look in this tap.")] = None,
    force: Annotated[bool, typer.Option(help="Reinstall (goes back to quarantine).")] = False,
) -> None:
    """Download a skill into quarantine (disabled until `auoreo skills approve <name>`)."""
    try:
        rec = asyncio.run(_hub().install(name, tap=tap, force=force))
    except HubError as exc:
        raise _fail(exc) from None
    console.print(
        f"[yellow]quarantined[/yellow] {name} sha256={rec['sha256'][:16]}… ({len(rec['files'])} files)"
    )
    if rec["has_scripts"]:
        console.print("[yellow]![/yellow] contains scripts — read them before approving")
    for problem in rec["lint"]:
        console.print(f"[red]lint[/red] {problem}")
    console.print(f"review ~/.auoreo/skills/.quarantine/{name}/ then run `auoreo skills approve {name}`")


@app.command("approve")
def approve(name: str) -> None:
    """Re-hash the quarantined copy against the lock and enable it."""
    try:
        rec = _hub().approve(name)
    except HubError as exc:
        raise _fail(exc) from None
    console.print(f"[green]✓[/green] {name} approved (sha256={rec['sha256'][:16]}…)")


@app.command("remove")
def remove(name: str) -> None:
    """Remove a hub-managed skill (quarantined or approved) and its lock entry."""
    try:
        _hub().remove(name)
    except HubError as exc:
        raise _fail(exc) from None
    console.print(f"[green]✓[/green] {name} removed")


@app.command("list")
def list_installed() -> None:
    """Hub-managed skills from skills.lock with their integrity."""
    hub = _hub()
    try:
        lock, state = hub.lock(), hub.verify()
    except HubError as exc:
        raise _fail(exc) from None
    for name, rec in sorted(lock.items()):
        console.print(
            f"{name}  {rec['status']}  {state.get(name)}  {rec['sha256'][:16]}…  [dim]{rec['tap']}[/dim]"
        )
    if not lock:
        console.print("no hub-managed skills")


@app.command("verify")
def verify() -> None:
    """Re-hash every hub-managed skill; exit 1 when one was modified or is missing."""
    try:
        state = _hub().verify()
    except HubError as exc:
        raise _fail(exc) from None
    for name, status in sorted(state.items()):
        console.print(f"{'[green]✓[/green]' if status == 'ok' else '[red]✗[/red]'} {name} {status}")
    if any(s != "ok" for s in state.values()):
        raise typer.Exit(1)


@app.command("sources")
def sources() -> None:
    """Where skills can come from: taps, plus skills offered by configured MCP servers."""
    for t in settings().g6.skills.taps:
        console.print(f"tap  {t}")
    for s in with_services(mcp_skills, memory=False, mcp=True):
        console.print(f"{s['source']}  {s['name']} — {s['description']}  [dim]{s['uri']}[/dim]")


@taps_app.callback()
def taps_list(ctx: typer.Context) -> None:
    """Print the configured taps."""
    if ctx.invoked_subcommand is None:
        for t in settings().g6.skills.taps:
            console.print(t)


def _set_taps(taps: list[str]) -> None:
    write_settings_value(settings().home, "g6.skills.taps", taps)


@taps_app.command("add")
def taps_add(url: str) -> None:
    """Add an HTTP(S) JSON index or a git URL (`git+https://…`, `….git`)."""
    if not url.startswith(("https://", "http://", "git+", "file://")) and not url.endswith(".git"):
        raise _fail(ValueError("a tap is an http(s) index URL or a git URL"))
    taps = list(settings().g6.skills.taps)
    if url not in taps:
        taps.append(url)
        _set_taps(taps)
    console.print(f"[green]✓[/green] taps: {', '.join(taps)}")


@taps_app.command("remove")
def taps_remove(url: str) -> None:
    taps = [t for t in settings().g6.skills.taps if t != url]
    _set_taps(taps)
    console.print(f"[green]✓[/green] taps: {', '.join(taps) or 'none'}")
