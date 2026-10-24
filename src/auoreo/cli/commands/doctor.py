"""`auoreo doctor`: report problems with a cause and remedy. Never repairs anything."""

from __future__ import annotations

import asyncio
import os
import socket
import sqlite3
import sys
from pathlib import Path
from typing import Annotated, Literal

import typer

from auoreo.cli.common import console, dump_json, settings
from auoreo.core.paths import bundled_root
from auoreo.harness.discovery import default_roots
from auoreo.harness.registry import Harness, HarnessError

Level = Literal["ok", "warn", "error"]


def _check(name: str, level: Level, detail: str, remedy: str = "") -> dict[str, str]:
    return {"check": name, "level": level, "detail": detail, "remedy": remedy}


def doctor(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Check the environment, definitions, database and credentials."""
    st = settings()
    out: list[dict[str, str]] = []
    py = sys.version_info
    out.append(
        _check(
            "python",
            "ok" if py >= (3, 12) else "error",
            sys.version.split()[0],
            "install Python 3.12+",
        )
    )
    sv = tuple(int(x) for x in sqlite3.sqlite_version.split("."))
    out.append(
        _check(
            "sqlite",
            "ok" if sv >= (3, 41) else "warn",
            sqlite3.sqlite_version,
            "SQLite ≥3.41 is needed for LIMIT-style vector KNN",
        )
    )
    try:
        import sqlite_vec

        db = sqlite3.connect(":memory:")
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        out.append(_check("sqlite-vec", "ok", db.execute("select vec_version()").fetchone()[0]))
    except Exception as exc:
        out.append(
            _check(
                "sqlite-vec",
                "warn",
                f"unavailable ({exc})",
                "memory runs FTS-only; use a Python build with loadable extensions",
            )
        )
    try:
        st.home.mkdir(parents=True, exist_ok=True)
        probe = st.home / ".doctor"
        probe.write_text("ok")
        probe.unlink()
        out.append(_check("home", "ok", str(st.home)))
    except OSError as exc:
        out.append(_check("home", "error", f"{st.home}: {exc}", "set AUOREO_HOME to a writable dir"))
    try:
        h = Harness(default_roots(st.home, Path.cwd(), bundled_root(), st.extra_roots))
        level: Level = "warn" if h.errors else "ok"
        counts = {k: len(v) for k, v in h.describe().items()}
        out.append(
            _check(
                "harness",
                level,
                f"{counts}; {len(h.errors)} overlay error(s)",
                "run `auoreo harness validate`",
            )
        )
    except HarnessError as exc:
        out.append(
            _check(
                "harness",
                "error",
                f"{len(exc.errors)} bundled error(s): {exc.errors[0]}",
                "fix the bundled definition",
            )
        )
    if st.gateway == "litellm":
        from auoreo.core.secrets import SecretStore

        has_key = SecretStore(st.home, st.keyring).get("OPENROUTER_API_KEY") is not None
        out.append(
            _check(
                "credentials",
                "ok" if has_key else "warn",
                "OPENROUTER_API_KEY present" if has_key else "OPENROUTER_API_KEY missing",
                "export OPENROUTER_API_KEY or `auoreo secrets set OPENROUTER_API_KEY`",
            )
        )
    else:
        out.append(_check("credentials", "ok", "fake gateway (no key needed)"))
    with socket.socket() as s:
        free = s.connect_ex((st.host, st.port)) != 0
    out.append(
        _check(
            "port",
            "ok" if free else "warn",
            f"{st.host}:{st.port} {'free' if free else 'in use'}",
            "another process is listening; pick --port or stop it",
        )
    )
    out.append(
        _check(
            "auth",
            "ok" if st.server_password or st.is_loopback else "error",
            "password set" if st.server_password else "no password (loopback only)",
            "set AUOREO_SERVER_PASSWORD before binding beyond loopback",
        )
    )
    if st.db_path.is_file():
        from auoreo.core.audit import AuditLog
        from auoreo.core.db import Database

        async def chain() -> int | None:
            db = Database(st.db_path)
            await db.init()
            try:
                return await AuditLog(db).verify()
            finally:
                await db.close()

        broken = asyncio.run(chain())
        out.append(
            _check(
                "audit",
                "ok" if broken is None else "error",
                "hash chain intact" if broken is None else f"chain broken at seq {broken}",
                "the audit log was modified outside auoreo",
            )
        )
    out.append(
        _check(
            "yolo",
            "warn" if os.environ.get("AUOREO_YOLO") else "ok",
            "AUOREO_YOLO set (prompts skipped; floors still apply)"
            if os.environ.get("AUOREO_YOLO")
            else "off",
        )
    )
    if as_json:
        dump_json(out)
    else:
        icon = {"ok": "[green]✓[/green]", "warn": "[yellow]![/yellow]", "error": "[red]✗[/red]"}
        for c in out:
            line = f"{icon[c['level']]} {c['check']:12} {c['detail']}"
            if c["level"] != "ok" and c["remedy"]:
                line += f"\n    → {c['remedy']}"
            console.print(line)
    if any(c["level"] == "error" for c in out):
        raise typer.Exit(1)
