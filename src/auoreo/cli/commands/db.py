"""`auoreo db upgrade|current|history|backup|restore|rotate-key|encrypt|recompute-costs` against
`<home>/state.db` (and, for backup/restore/rotate-key/encrypt, `<home>/memory.db`)."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.markup import escape

from auoreo.cli.common import console, dump_json, err_console, settings
from auoreo.core.crypto_columns import DbKeyring, parse_keys
from auoreo.core.db import SCHEMA_VERSION, alembic_config, migrate

app = typer.Typer(no_args_is_help=True)


def _url() -> str:
    return f"sqlite:///{settings().db_path}"


def _keyring() -> DbKeyring:
    st = settings()
    return DbKeyring(st.db_path.parent, st.keyring)


@app.command("upgrade")
def upgrade(revision: Annotated[str, typer.Argument()] = "head") -> None:
    """Migrate state.db to REVISION (default: head). Startup does this automatically."""
    from auoreo.core import sqlcipher

    path = settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    migrate(path, revision, keyring=_keyring() if sqlcipher.is_encrypted_file(path) else None)
    console.print(f"[green]state.db at {revision}[/green] ({path})")


@app.command("current")
def current() -> None:
    """Show the revision state.db is at."""
    from alembic import command

    command.current(alembic_config(_url()), verbose=False)


@app.command("history")
def history() -> None:
    """List the migration chain."""
    from alembic import command

    command.history(alembic_config(_url()), verbose=False)


def _rows(target: str, session: str | None, tail: int, as_json: bool) -> None:
    """--- I5: the last N rows of sessions|events|audit (read-only, redacted)."""
    from auoreo.core.db_rows import DbInspectError, inspect_rows

    try:
        rows = inspect_rows(settings(), target, session, tail)
    except DbInspectError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2 if "target" in str(exc) else 1) from exc
    if as_json:
        dump_json(rows)
        return
    for r in rows:
        console.print(escape("  ".join(f"{k}={json.dumps(v, default=str)}" for k, v in r.items())))
    if not rows:
        console.print(f"no {target} rows" + (f" for session {session}" if session else ""))


@app.command("inspect")  # --- G5a
def inspect(
    target: Annotated[
        str | None, typer.Argument(help="sessions | events | audit (I5); omit for the summary.")
    ] = None,
    session: Annotated[str | None, typer.Option("--session", "-s", help="Only this session.")] = None,
    tail: Annotated[int, typer.Option("--tail", "-n", help="Last N rows (oldest first).", min=1)] = 20,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Read-only: schema version, Alembic head/current, row counts per table, file sizes —
    or, with a TARGET, the last N rows of that table."""
    from auoreo.core.debuginfo import db_inspect

    if target is not None:
        _rows(target, session, tail, as_json)
        return
    info = db_inspect(settings())
    if as_json:
        print(json.dumps(info, indent=2))
        return
    console.print(f"path            {info['path']}{'' if info['exists'] else ' (missing)'}")
    console.print(f"schema_version  {info['schema_version']} (code {info['code_schema_version']})")
    console.print(f"alembic         current {info['alembic_current']} · head {info['alembic_head']}")
    for name, size in info["files"].items():
        console.print(f"file            {name:14} {size:>12,} bytes")
    for name, count in info["tables"].items():
        console.print(f"table           {name:28} {count:>10,} rows")
    if info.get("note"):
        console.print(f"[yellow]{info['note']}[/yellow]")


# ------------------------------------------------------------------ W1-E: backups + keys
def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    if os.name == "posix":
        path.chmod(0o600)


def backup_file(src: Path, dst: Path, keys: list[bytes] | None = None) -> None:
    """SQLite online backup API (consistent while the server runs). `keys` = SQLCipher file:
    both ends are opened with the same key, so the copy stays encrypted."""
    from auoreo.core import sqlcipher

    dst.unlink(missing_ok=True)
    if keys is not None:
        s, d = sqlcipher.sync_connect(src, keys), _open_like(src, dst, keys)
    else:
        s, d = sqlite3.connect(src), sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    if os.name == "posix":
        dst.chmod(0o600)


def _open_like(src: Path, dst: Path, keys: list[bytes]) -> Any:
    from auoreo.core import sqlcipher

    for key in keys:  # the key that opens src is the one the copy must carry
        try:
            sqlcipher.sync_connect(src, [key]).close()
        except Exception:
            continue
        return sqlcipher.sync_connect(dst, [key])
    raise typer.BadParameter(f"{src.name} cannot be opened with the database key")


def run_backup(home: Path, dest: Path, keyring: DbKeyring, with_key: Path | None) -> dict[str, Any]:
    from auoreo.core import sqlcipher

    st = settings()
    dest.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        dest.chmod(0o700)
    files: list[str] = []
    state = home / "state.db"
    encrypted = sqlcipher.is_encrypted_file(state)
    if state.is_file():
        backup_file(state, dest / "state.db", keyring.load() if encrypted else None)
        files.append("state.db")
    memory = home / "memory.db"
    mem_encrypted = sqlcipher.is_encrypted_file(memory)
    if memory.is_file():  # F6: an encrypted memory.db stays encrypted in the copy
        backup_file(memory, dest / "memory.db", keyring.load() if mem_encrypted else None)
        files.append("memory.db")
    keys = keyring.load()
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "schema_version": SCHEMA_VERSION,
        "db_encryption": st.db_encryption,
        "state_db_sqlcipher": encrypted,
        "memory_db_sqlcipher": mem_encrypted,
        "files": files,
        "key_in_backup": False,
        "key_ring_size": len(keys),
    }
    _write_private(dest / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    if with_key is not None:
        if not keys:
            raise typer.BadParameter("no database key exists yet (nothing is encrypted)")
        _write_private(with_key, ",".join(k.decode() for k in keys) + "\n")
    return manifest


@app.command("backup")
def backup(
    dest: Annotated[Path, typer.Argument(help="Directory to write state.db, memory.db, manifest.json.")],
    with_key: Annotated[
        Path | None, typer.Option("--with-key", help="Also export the database key ring here (0600).")
    ] = None,
) -> None:
    """Online backup of state.db + memory.db. The key is exported only with --with-key; keep it
    apart from the backup (spec L1069: backups carry the key separately)."""
    st = settings()
    dest = dest.expanduser().resolve()
    if with_key is not None and with_key.expanduser().resolve().parent == dest:
        err_console.print("[yellow]warning: the key file sits inside the backup directory[/yellow]")
    manifest = run_backup(st.home, dest, _keyring(), with_key)
    console.print(f"[green]backup written[/green] {dest}: {', '.join(manifest['files']) or 'nothing'}")
    if with_key is not None:
        console.print(f"[green]key ring exported[/green] {with_key} (0600, {manifest['key_ring_size']} keys)")


def run_restore(src: Path, home: Path, keyring: DbKeyring, key: Path | None) -> list[str]:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    restored: list[str] = []
    for name in ("state.db", "memory.db"):
        if not (src / name).is_file():
            continue
        target = home / name
        if target.is_file():
            target.replace(target.with_name(f"{name}.pre-restore-{stamp}.bak"))
        for suffix in ("-wal", "-shm"):
            Path(str(target) + suffix).unlink(missing_ok=True)
        shutil.copy2(src / name, target)
        restored.append(name)
    if key is not None:
        incoming = parse_keys(key.read_text())
        keyring.save(incoming + [k for k in keyring.load() if k not in incoming])
    return restored


@app.command("restore")
def restore(
    src: Annotated[Path, typer.Argument(help="A directory written by `auoreo db backup`.")],
    yes: Annotated[bool, typer.Option("--yes", help="Confirm: replaces state.db / memory.db.")] = False,
    key: Annotated[Path | None, typer.Option("--key", help="Key ring file from --with-key.")] = None,
) -> None:
    """Restore a backup into AUOREO_HOME (stop the server first). Current files are kept as
    `*.pre-restore-<ts>.bak`; a --key file is merged into the key ring (newest first)."""
    if not yes:
        err_console.print("[red]restore replaces state.db and memory.db; re-run with --yes[/red]")
        raise typer.Exit(2)
    st = settings()
    restored = run_restore(src.expanduser(), st.home, _keyring(), key)
    console.print(f"[green]restored[/green] {', '.join(restored) or 'nothing'} into {st.home}")


@app.command("rotate-key")
def rotate_key() -> None:
    """Add a new primary database key (MultiFernet). Old keys stay in the ring: audit ciphertext
    is immutable. A SQLCipher state.db / memory.db is re-keyed (stop the server first)."""
    from auoreo.core import sqlcipher

    kr = _keyring()
    old = kr.load()
    keys = kr.rotate()
    st = settings()
    for path in (st.db_path, st.home / "memory.db"):
        if sqlcipher.is_encrypted_file(path):
            sqlcipher.rekey(path, old, keys[0])
            console.print(f"[green]{path.name} re-keyed[/green]")
    console.print(f"[green]new primary key[/green]; ring has {len(keys)} keys")
    console.print("back the ring up with `auoreo db backup <dir> --with-key <file>`")


@app.command("encrypt")
def encrypt(
    yes: Annotated[bool, typer.Option("--yes", help="Confirm the in-place conversion.")] = False,
) -> None:
    """Convert plaintext state.db and memory.db to SQLCipher (`sqlcipher_export`); a plaintext
    backup of each is kept. Stop the server first, then set db_encryption=sqlcipher."""
    from auoreo.core import sqlcipher

    ok, info = sqlcipher.check()
    if not ok:
        err_console.print(f"[red]{info}; use db_encryption=columns instead[/red]")
        raise typer.Exit(2)
    if not yes:
        err_console.print("[red]this rewrites state.db and memory.db in place; re-run with --yes[/red]")
        raise typer.Exit(2)
    st = settings()
    done = 0
    for path in (st.db_path, st.home / "memory.db"):
        if not path.is_file() or sqlcipher.is_encrypted_file(path):
            console.print(f"{path.name}: {'already encrypted' if path.is_file() else 'absent'}, skipped")
            continue
        bak = sqlcipher.encrypt_file(path, _keyring())
        done += 1
        console.print(f"[green]{path.name} encrypted ({info})[/green]")
        console.print(f"plaintext backup: {bak} (delete it once verified)")
    if not done:
        console.print("nothing to convert")


@app.command("recompute-costs")  # --- M7 1.4
def recompute_costs(
    days: Annotated[
        int | None, typer.Option("--days", min=1, help="Only rows of the last N days (default: all).")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report what would change; write nothing.")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
    all_rows: Annotated[
        bool, typer.Option("--all", help="Rescan rows below the last run's high-water mark too.")
    ] = False,
) -> None:
    """Re-price estimated usage rows that have cached or cache-write tokens with the cache-aware
    rates (needs m7.token.cached_pricing). Prices are today's; provider-reported costs and rows
    without a current price are kept. Runs only on request, never at startup; safe to repeat.
    A completed run remembers the last row it scanned; the next run starts after it (`--all`:
    from the first row)."""
    from auoreo.cli.common import with_services
    from auoreo.core.services import Services
    from auoreo.core.usage_reprice import RepriceReport
    from auoreo.core.usage_reprice import recompute_costs as run

    st = settings()
    if not st.m7.token.cached_pricing:
        err_console.print("[red]m7.token.cached_pricing is off: costs would be recomputed unchanged[/red]")
        raise typer.Exit(2)

    async def go(svc: Services) -> RepriceReport:
        return await run(svc.db, svc.gateway.price, days=days, dry_run=dry_run, all_rows=all_rows)

    report = with_services(go, st, memory=False)
    if as_json:
        print(json.dumps(report.as_dict(), indent=2))
        return
    verb = "would re-price" if dry_run else "re-priced"
    scope = "every row" if all_rows else "rows after the last run's mark"
    window = f"last {days} days, {scope}" if days else scope
    console.print(
        f"{verb} {report.repriced} of {report.scanned} estimated rows with cache tokens ({window}): "
        f"${report.old_total:.6f} -> ${report.new_total:.6f}"
    )
    console.print(f"unchanged {report.unchanged} · no current price {report.unpriced} · mark {report.mark}")
