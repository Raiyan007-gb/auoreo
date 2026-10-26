from __future__ import annotations

import json
import os
import secrets as pysecrets
from pathlib import Path
from typing import Annotated

import typer

from auoreo.cli.common import settings
from auoreo.core.redaction import mask
from auoreo.core.secrets import SecretStore

app = typer.Typer(no_args_is_help=True)


def _store() -> SecretStore:
    st = settings()
    return SecretStore(st.home, st.keyring)


@app.command("set")
def set_(name: str) -> None:
    """Store a secret (value read from a hidden prompt, never from argv)."""
    value = typer.prompt(f"value for {name}", hide_input=True)
    _store().set(name, value)
    typer.echo(f"stored {name}")


@app.command("list")
def list_() -> None:
    """List secret names (values are masked)."""
    store = _store()
    for name in store.names():
        typer.echo(f"{name}  {mask(store.get(name) or '')}")


@app.command("delete")
def delete(name: str) -> None:
    """Delete a secret."""
    typer.echo("deleted" if _store().delete(name) else "not found")


# --- G5a: rotate + export (values never reach stdout unless `--stdout --yes`)
@app.command("rotate")
def rotate(
    name: str,
    generate: Annotated[
        bool, typer.Option("--generate", help="Store a random value instead of prompting.")
    ] = False,
    length: Annotated[int, typer.Option(help="Random bytes for --generate.", min=16, max=256)] = 32,
) -> None:
    """Replace a secret; the previous value is kept as `<name>#prev`. Nothing is printed."""
    store = _store()
    old = store.get(name)
    value = (
        pysecrets.token_urlsafe(length)
        if generate
        else typer.prompt(f"new value for {name}", hide_input=True)
    )
    if old:
        store.set(f"{name}#prev", old)
    store.set(name, value)
    kept = f" (previous kept as {name}#prev)" if old else ""
    typer.echo(f"rotated {name}{kept}")
    if generate:
        typer.echo(
            "the generated value is not shown; read it with `auoreo secrets export --stdout --yes`", err=True
        )


@app.command("export")
def export(
    out: Annotated[Path | None, typer.Option("--out", "-o", help="File to write (mode 0600).")] = None,
    stdout: Annotated[bool, typer.Option("--stdout", help="Print instead of writing a file.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm exporting plaintext secret values.")] = False,
) -> None:
    """Export every stored secret as plaintext JSON. Requires --yes; a 0600 file by default."""
    if not yes:
        typer.echo("refusing: export writes plaintext secrets; re-run with --yes", err=True)
        raise typer.Exit(2)
    store = _store()
    data = {n: store.get(n, by="cli:export") for n in store.names()}
    text = json.dumps(data, indent=2) + "\n"
    if stdout:
        typer.echo(text, nl=False)
        return
    path = out or Path.cwd() / "auoreo-secrets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)  # O_CREAT keeps an existing file's mode; start fresh
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    typer.echo(f"wrote {len(data)} secret(s) to {path} (mode 0600)", err=True)
