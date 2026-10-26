"""I6: `auoreo plugin search|pip-add|entry-points|trust` (spec §25.15 trust tiers + registry).

Registered on the existing `auoreo plugin` group (`cli/commands/plugin.py` stays untouched):

- `search [query]` — the configured taps (`i6.plugins.taps`), cached, offline-safe.
- `pip-add <package>[==ver]` — an allow-listed, SHA-256-pinned `oreo-plugin-*` /
  `auoreo-plugin-*` distribution into its own venv; lands quarantined (verify → enable).
- `entry-points` — `importlib.metadata` entry points (`auoreo.plugin`, `oreo.plugin`) in this
  environment, listed without importing; they never run from here.
- `trust` — tier, registry, signer and integrity of every discovered plugin.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table

from auoreo.cli import audit_cli
from auoreo.cli.commands.plugin import app
from auoreo.cli.common import console, dump_json, err_console, settings
from auoreo.core.paths import bundled_root, ensure_home
from auoreo.harness.discovery import default_roots
from auoreo.harness.registry import Harness

JsonOpt = Annotated[bool, typer.Option("--json")]


def _table(rows: list[dict[str, Any]], cols: tuple[str, ...], caption: str | None = None) -> None:
    table = Table(caption=caption)
    for col in cols:
        table.add_column(col, overflow="fold")
    for r in rows:
        table.add_row(*(str(r.get(c) if r.get(c) is not None else "") for c in cols))
    console.print(table)


@app.command("search")
def search(query: Annotated[str, typer.Argument()] = "", as_json: JsonOpt = False) -> None:
    """Search the plugin taps (`i6.plugins.taps`); only hash-pinned entries are listed."""
    from auoreo.plugins.registry import search as do_search

    st = settings()
    hits, errors = asyncio.run(do_search(st, query))
    rows = [
        {
            "package": h.package,
            "version": h.version,
            "signed": "yes" if h.signature.get("sig") else "no",
            "tap": h.tap,
            "description": h.description,
        }
        for h in hits
    ]
    if as_json:
        dump_json({"plugins": rows, "errors": errors})
        return
    for e in errors:
        err_console.print(f"[yellow]{e}[/yellow]")
    _table(rows, ("package", "version", "signed", "tap", "description"), None if rows else "no matches")


@app.command("pip-add")
def pip_add(
    spec: Annotated[str, typer.Argument(help="auoreo-plugin-<name>[==<version>] (allow-listed + pinned).")],
    force: Annotated[bool, typer.Option("--force", help="Replace an existing copy.")] = False,
) -> None:
    """Install an allow-listed, SHA-256-pinned pip plugin into its own venv (quarantined)."""
    from auoreo.plugins.install import InstallError
    from auoreo.plugins.pip_install import TrustError
    from auoreo.plugins.pip_install import pip_add as do_add

    st = settings()
    ensure_home(st.home)
    try:
        res = asyncio.run(do_add(st, spec, force=force))
    except (TrustError, InstallError) as exc:
        audit_cli.record(st, "plugin.pip_add", {"spec": spec[:200]}, decision="deny", reason=str(exc)[:300])
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    audit_cli.record(st, "plugin.pip_add", {k: res[k] for k in ("name", "package", "version", "registry")})
    console.print(
        f"installed {res['package']}=={res['version']} as '{res['name']}' (registry {res['registry']}, "
        f"entry {res['entry']}), quarantined"
    )
    console.print(f"next: `auoreo plugin verify {res['name']}` then `auoreo plugin enable {res['name']}`")


@app.command("entry-points")
def entry_points(as_json: JsonOpt = False) -> None:
    """`auoreo.plugin` / `oreo.plugin` entry points in this environment (listed, never imported)."""
    from auoreo.plugins.trust import entry_point_plugins

    rows = entry_point_plugins(settings().i6.plugins.entry_point_groups)
    if as_json:
        dump_json(rows)
        return
    _table(rows, ("name", "value", "dist", "version", "group", "status"), None if rows else "none found")


def trust_rows() -> list[dict[str, Any]]:
    from auoreo.plugins import install as inst
    from auoreo.plugins.quarantine import is_quarantined
    from auoreo.plugins.trust import installed_problem, provenance

    st = settings()
    home = ensure_home(st.home)
    harness = Harness(default_roots(home, Path.cwd(), bundled_root(), st.extra_roots))
    rows = []
    for name, d in sorted(harness.plugins.items()):
        prov = provenance(st, name, d.path.parent, d.manifest.version)
        problem = installed_problem(home, name)
        rows.append(
            {
                "name": name,
                "tier": prov.get("tier"),
                "registry": prov.get("registry"),
                "signed_by": prov.get("signed_by") or ("-" if prov.get("tier") != "bundled" else "UNSIGNED"),
                "integrity": problem or inst.verify(home, name, d.path.parent),
                "quarantined": "yes" if is_quarantined(home, name) else "no",
            }
        )
    return rows


@app.command("trust")
def trust(as_json: JsonOpt = False) -> None:
    """Trust tier, registry, signer and venv integrity of every discovered plugin."""
    rows = trust_rows()
    if as_json:
        dump_json(rows)
        return
    _table(rows, ("name", "tier", "registry", "signed_by", "integrity", "quarantined"))
