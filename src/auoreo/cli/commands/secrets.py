from __future__ import annotations

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
