"""`auoreo secrets set|list|delete|rotate|export|import` (spec §7, §25.14).

Values never reach argv or stdout: `set`/`rotate` prompt hidden, `list` prints names (or a
`head4…tail4` mask with `--masked`), `export` writes an age-encrypted backup to one or more
`--recipient`s. A plaintext export exists only behind `--insecure-plaintext --yes`.
"""

from __future__ import annotations

import os
import re
import secrets as pysecrets
import time
from pathlib import Path
from typing import Annotated

import typer

from auoreo.cli import audit_cli
from auoreo.cli.common import settings
from auoreo.core import secrets_backup
from auoreo.core.secrets import ROTATION_SUFFIXES, SecretStore

app = typer.Typer(no_args_is_help=True)
_DURATION = re.compile(r"^\s*(\d+)\s*([smhd]?)\s*$")
_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86_400}
_META = ("#rotated_at", "#prev_expires")


def _store() -> SecretStore:
    st = settings()
    return SecretStore(st.home, st.keyring)


def parse_duration(text: str) -> int:
    """`90`, `30s`, `15m`, `24h`, `7d` → seconds."""
    m = _DURATION.match(text)
    if m is None:
        raise typer.BadParameter(f"not a duration: {text!r} (use e.g. 30m, 24h, 7d)")
    return int(m.group(1)) * _UNITS[m.group(2)]


def mask4(value: str) -> str:
    return "****" if len(value) < 12 else f"{value[:4]}…{value[-4:]}"


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)  # O_CREAT keeps an existing file's mode; start fresh
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


@app.command("set")
def set_(name: str) -> None:
    """Store a secret (value read from a hidden prompt, never from argv)."""
    value = typer.prompt(f"value for {name}", hide_input=True)
    _store().set(name, value)
    typer.echo(f"stored {name}")


@app.command("list")
def list_(
    masked: Annotated[
        bool, typer.Option("--masked/--names-only", help="Show head4…tail4 instead of names only.")
    ] = False,
) -> None:
    """List secret names (default). `--masked` adds a head4…tail4 hint, never the value."""
    store = _store()
    store.purge_expired()
    for name in store.names():
        if name.endswith(_META):
            continue
        typer.echo(f"{name}  {mask4(store.get(name) or '')}" if masked else name)


@app.command("delete")
def delete(name: str) -> None:
    """Delete a secret (and its rotation leftovers)."""
    store = _store()
    found = store.delete(name)
    for suffix in ROTATION_SUFFIXES:
        store.delete(f"{name}{suffix}")
    typer.echo("deleted" if found else "not found")


@app.command("rotate")
def rotate(
    name: str,
    grace: Annotated[
        str | None,
        typer.Option("--grace", help="Keep the old value as <name>#prev this long (e.g. 24h; 0 = drop it)."),
    ] = None,
    generate: Annotated[
        bool, typer.Option("--generate", help="Store a random value instead of prompting.")
    ] = False,
    length: Annotated[int, typer.Option(help="Random bytes for --generate.", min=16, max=256)] = 32,
) -> None:
    """Replace a secret; the previous value still resolves as `<name>#prev` until the grace
    window ends (then it is purged). Nothing is printed."""
    st = settings()
    grace_s = parse_duration(grace) if grace is not None else st.h1b.rotate_grace_s
    store = SecretStore(st.home, st.keyring)
    value = (
        pysecrets.token_urlsafe(length)
        if generate
        else typer.prompt(f"new value for {name}", hide_input=True)
    )
    kept = store.rotate(name, value, grace_s)
    until = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + grace_s))
    typer.echo(f"rotated {name}" + (f" (previous kept as {name}#prev until {until})" if kept else ""))
    if generate:
        typer.echo("the generated value is not shown; back it up with `auoreo secrets export`", err=True)


@app.command("export")
def export(
    to: Annotated[Path | None, typer.Option("--to", "--out", "-o", help="Backup file (mode 0600).")] = None,
    recipient: Annotated[
        list[str] | None, typer.Option("--recipient", "-r", help="age1… / ssh public key (repeatable).")
    ] = None,
    insecure_plaintext: Annotated[
        bool, typer.Option("--insecure-plaintext", help="Write unencrypted JSON (needs --yes).")
    ] = False,
    stdout: Annotated[bool, typer.Option("--stdout", help="With --insecure-plaintext: print.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm a plaintext export.")] = False,
) -> None:
    """Back up every stored secret, age-encrypted to each `--recipient`. The Fernet key is
    never exported; an audit row `secrets_export{recipients_hash}` records the receipt."""
    st = settings()
    store = SecretStore(st.home, st.keyring)
    store.purge_expired()
    data = {n: v for n in store.names() if not n.endswith(_META) and (v := store.get(n, by="cli:export"))}
    recipients = list(recipient or [])
    if insecure_plaintext:
        if not yes:
            typer.echo("refusing: --insecure-plaintext writes secrets in clear; add --yes", err=True)
            raise typer.Exit(2)
        blob, receipt = secrets_backup.payload(data), "plaintext"
    else:
        if stdout:
            typer.echo("refusing: --stdout is only for --insecure-plaintext", err=True)
            raise typer.Exit(2)
        if not recipients:
            typer.echo("refusing: pass --recipient age1… (or --insecure-plaintext --yes)", err=True)
            raise typer.Exit(2)
        try:
            blob = secrets_backup.encrypt(data, recipients)
        except secrets_backup.BackupError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(1) from exc
        receipt = secrets_backup.recipients_hash(recipients)
    detail = {"recipients_hash": receipt, "recipients": len(recipients), "entries": len(data)}
    if not audit_cli.record(
        st, "secrets_export", detail, reason="plaintext" if insecure_plaintext else "age"
    ):
        typer.echo("warning: the secrets_export audit row could not be written", err=True)
    if stdout:
        typer.echo(blob.decode(), nl=False)
        return
    path = to or Path.cwd() / ("auoreo-secrets.json" if insecure_plaintext else "auoreo-secrets.age")
    _write_private(path, blob)
    typer.echo(f"wrote {len(data)} secret(s) to {path} (mode 0600)", err=True)
    if not insecure_plaintext:
        typer.echo(f"recipients sha256 {receipt}", err=True)


@app.command("import")
def import_(
    src: Annotated[Path, typer.Argument(help="Backup written by `secrets export`.")],
    identity: Annotated[
        Path | None, typer.Option("--identity", "-i", help="age identity / OpenSSH private key file.")
    ] = None,
    insecure_plaintext: Annotated[
        bool, typer.Option("--insecure-plaintext", help="The backup is unencrypted JSON.")
    ] = False,
    overwrite: Annotated[bool, typer.Option("--overwrite", help="Replace names that already exist.")] = False,
) -> None:
    """Restore a backup into this machine's `secrets.db` (encrypted with the local key)."""
    st = settings()
    try:
        raw = src.read_bytes()
        if insecure_plaintext:
            entries = secrets_backup.parse(raw)
        elif identity is None:
            typer.echo("refusing: pass --identity <file> (or --insecure-plaintext)", err=True)
            raise typer.Exit(2)
        else:
            entries = secrets_backup.decrypt(raw, identity)
    except (OSError, secrets_backup.BackupError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    store = SecretStore(st.home, st.keyring)
    existing = set(store.names())
    written = skipped = 0
    for name, value in entries.items():
        if name in existing and not overwrite:
            skipped += 1
            continue
        store.set(name, value)
        written += 1
    audit_cli.record(st, "secrets_import", {"entries": written, "skipped": skipped})
    typer.echo(
        f"imported {written} secret(s)" + (f", skipped {skipped} existing (--overwrite)" if skipped else "")
    )
