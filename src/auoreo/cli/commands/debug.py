"""`auoreo debug bundle|snapshot` — local, redacted diagnostics archives (spec §20.3).

Redaction is forced (patterns + every known secret value); `upload` (H4) sends only to the
configured `cli2.debug_upload_url`, after a redaction gate and a confirmation."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from auoreo.cli.common import console, err_console, settings, with_services
from auoreo.core import debuginfo
from auoreo.core import sessions_ops as ops
from auoreo.core.services import Services

app = typer.Typer(no_args_is_help=True)


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


@app.command("bundle")
def bundle(
    out: Annotated[
        Path | None, typer.Option("--out", "-o", help="Zip path (default ./auoreo-debug-<ts>.zip).")
    ] = None,
) -> None:
    """Zip redacted settings, doctor, db stats (counts only), recent events/logs and versions."""
    st = settings()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out or Path.cwd() / f"auoreo-debug-{stamp}.zip"
    members = asyncio.run(debuginfo.build_bundle(st, path))
    console.print(f"wrote {path} ({len(members)} files, mode 0600; nothing uploaded)")


@app.command("snapshot")
def snapshot(
    session_id: str,
    fmt: Annotated[str, typer.Option("--format", "-f", help="json | md")] = "json",
    out: Annotated[Path | None, typer.Option("--out", "-o", help="File ('-' = stdout).")] = None,
) -> None:
    """Export one session's transcript with secrets redacted (forced)."""
    if fmt not in {"json", "md"}:
        err_console.print("[red]--format must be json or md[/red]")
        raise typer.Exit(2)
    st = settings()

    async def go(svc: Services) -> str:
        return await ops.export_session(svc, session_id, fmt)

    try:
        text = with_services(go, st, memory=False)
    except KeyError as exc:
        err_console.print(f"[red]{str(exc).strip(chr(39) + chr(34))}[/red]")
        raise typer.Exit(1) from exc
    text = debuginfo.Scrubber(st).text(text)
    if out is not None and str(out) == "-":
        print(text, end="")
        return
    path = out or Path.cwd() / f"snapshot-{session_id}.{fmt}"
    _write_private(path, text)
    console.print(f"wrote {path} (redacted)")


# --- H4: opt-in upload to the configured endpoint only (forced redaction + gate + confirm)
@app.command("upload")
def upload(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
    keep: Annotated[
        Path | None, typer.Option("--keep", help="Also keep the uploaded zip at this path.")
    ] = None,
) -> None:
    """Upload a redacted debug bundle to `cli2.debug_upload_url` (refuses when unset)."""
    from auoreo.cli.debug_upload import upload as do_upload

    do_upload(settings(), yes=yes, keep=keep)
