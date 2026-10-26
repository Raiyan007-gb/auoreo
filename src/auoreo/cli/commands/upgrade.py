"""`auoreo upgrade`: signed, channel-aware self-upgrade (logic in `auoreo.core.upgrade`)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape

from auoreo import __version__
from auoreo.cli.common import console, err_console, settings
from auoreo.core import upgrade as up
from auoreo.core.settings import Settings


def upgrade(
    channel: Annotated[
        str | None, typer.Option(help="stable | beta | dev (default: settings.update_channel).")
    ] = None,
    check: Annotated[bool, typer.Option("--check", help="Only report whether an update exists.")] = False,
    do_rollback: Annotated[
        bool, typer.Option("--rollback", help="Restore the previous binary (auoreo.prev).")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
    target: Annotated[
        str | None, typer.Option("--target", help="Install exactly this version (e.g. 0.3.1).")
    ] = None,
) -> None:
    """Upgrade this install from the signed release manifest (binary swap, or the pip/uv command)."""
    st = settings()
    chan = channel or st.update_channel
    method = up.install_method()
    try:
        if do_rollback:
            _rollback(st.home, method, yes)
            return
        if target and not _target_matches(st, chan, method, target):
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


# --- H4: `--target <version>` (spec §18). The signed manifest names one version per channel:
# a matching target installs normally; any other version becomes a pinned package-manager
# command (pip / uv / pipx / checkout) or, for a binary install, an honest refusal.
PINNED = {
    "uv-tool": "uv tool install --force auoreo=={v}",
    "pipx": "pipx install --force auoreo=={v}",
    "pip": sys.executable + " -m pip install auoreo=={v}",
    "checkout": "git fetch --tags && git checkout v{v} && uv sync",
}


def _target_matches(st: Settings, chan: str, method: str, target: str) -> bool:
    """True → continue with the normal upgrade (the manifest offers exactly `target`)."""
    if up.parse_version(target) == up.parse_version(__version__):  # UpgradeError on garbage
        console.print(f"auoreo {__version__} is already installed")
        return False
    pre = asyncio.run(_run(st.home, chan, st.update_url, st.update_pubkeys, check_only=True))
    if pre.latest == target:
        return True
    if method != "binary":
        cmd = PINNED.get(method, PINNED["pip"]).format(v=target)
        offer = f"the {chan} manifest offers {pre.latest}, not {target}."
        console.print(escape(f"{offer} This is a {method} install; run:\n  {cmd}"))
        return False
    raise up.UpgradeError(
        f"the {chan} manifest offers {pre.latest}, not {target}; signed binaries are only installed "
        "from the manifest (download that release manually, or pick its channel with --channel)"
    )
