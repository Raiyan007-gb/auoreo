"""`auoreo upgrade`: signed, channel-aware self-upgrade (logic in `auoreo.core.upgrade`)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape

from auoreo.cli.common import console, err_console, settings
from auoreo.core import upgrade as up


def upgrade(
    channel: Annotated[
        str | None, typer.Option(help="stable | beta | dev (default: settings.update_channel).")
    ] = None,
    check: Annotated[bool, typer.Option("--check", help="Only report whether an update exists.")] = False,
    do_rollback: Annotated[
        bool, typer.Option("--rollback", help="Restore the previous binary (auoreo.prev).")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
) -> None:
    """Upgrade this install from the signed release manifest (binary swap, or the pip/uv command)."""
    st = settings()
    chan = channel or st.update_channel
    method = up.install_method()
    try:
        if do_rollback:
            _rollback(st.home, method, yes)
            return
        if not check and method == "binary" and not yes:
            pre = asyncio.run(_run(st.home, chan, st.update_url, st.update_pubkeys, check_only=True))
            if pre.status == "up-to-date":
                console.print(f"auoreo {pre.current} is up to date ({chan})")
                return
            typer.confirm(f"Upgrade auoreo {pre.current} → {pre.latest} ({chan})?", abort=True)
        out = asyncio.run(_run(st.home, chan, st.update_url, st.update_pubkeys, check_only=check))
    except up.UpgradeError as exc:
        err_console.print(f"[red]upgrade refused:[/red] {escape(str(exc))}")
        raise typer.Exit(1) from exc
    _report(out, chan, method)


async def _run(home: Path, chan: str, url: str, keys: list[str], *, check_only: bool) -> up.Outcome:
    return await up.run_upgrade(
        home=home, channel=chan, url_template=url, extra_pubkeys=keys, check_only=check_only
    )


def _report(out: up.Outcome, chan: str, method: str) -> None:
    lines = {
        "up-to-date": f"auoreo {out.current} is up to date ({chan})",
        "available": f"update available: {out.current} → {out.latest} ({chan}, {method} install)",
        "command": f"update available: {out.current} → {out.latest} ({chan}). This is a {method} "
        f"install; run:\n  {out.message}",
        "installed": f"installed {out.latest}: {out.message}\nRestart running auoreo processes "
        "(e.g. `auoreo serve`) to use it.",
        "staged": f"{out.latest} downloaded and verified. {out.message}",
    }
    console.print(escape(lines[out.status]))
    if out.receipt is not None:
        console.print(f"receipt: {out.receipt}")


def _rollback(home: Path, method: str, yes: bool) -> None:
    if method != "binary":
        raise up.UpgradeError(
            f"--rollback swaps binaries; this is a {method} install (reinstall the previous version)"
        )
    binary = Path(sys.executable)
    if not yes:
        typer.confirm(f"Restore {up.prev_path(binary).name} over {binary.name}?", abort=True)
    console.print(escape(up.rollback(home, binary, windows=sys.platform == "win32")))
