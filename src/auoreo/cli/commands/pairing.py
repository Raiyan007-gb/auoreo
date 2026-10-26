"""I5: top-level `auoreo pairing list|approve|revoke` (spec §18 `oreo …/pairing`).

Chat pairing for connector plugins (Telegram first): an unknown chat gets a 6-digit code;
`approve <platform> <code>` allowlists that chat, `revoke` removes one. A running server is
asked first (`/api/plugins/<name>/pair`), else the pairing store on disk is used. Thin
wrapper over `plugins.connectors.PairingStore`."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from auoreo.cli.commands.messaging import PLATFORMS, UrlOpt
from auoreo.cli.commands.plugin import _api
from auoreo.cli.common import console, dump_json, err_console, settings
from auoreo.cli.output import want_json
from auoreo.core.paths import ensure_home
from auoreo.plugins.connectors import PairingStore

app = typer.Typer(no_args_is_help=True)
JsonOpt = Annotated[bool, typer.Option("--json")]


def _plugin(platform: str) -> str:
    p = PLATFORMS.get(platform)
    if p is None or not p.connector:
        known = ", ".join(k for k, v in PLATFORMS.items() if v.connector)
        err_console.print(f"[red]'{platform}' has no inbound connector to pair (known: {known})[/red]")
        raise typer.Exit(2)
    return p.plugin


@app.command("list")
def list_(
    platform: Annotated[str | None, typer.Argument(help="telegram (default: every connector).")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Pending pairing codes (chat + user, never the code) and the allowlist per platform."""
    st = settings()
    names = [_plugin(platform)] if platform else [k for k, v in PLATFORMS.items() if v.connector]
    rows: list[dict[str, Any]] = []
    for name in names:
        store = PairingStore(ensure_home(st.home), name)
        rows.append({"platform": name, "allowlist": store.allowlist(), "pending": store.pending()})
    if want_json(as_json):
        dump_json(rows)
        return
    for r in rows:
        console.print(f"{r['platform']}: {len(r['allowlist'])} allowlisted, {len(r['pending'])} pending")
        for chat in r["allowlist"]:
            console.print(f"  allowed  {chat}")
        for p in r["pending"]:
            console.print(f"  pending  {p['chat_id']} ({p['user'] or 'unknown user'})")


@app.command("approve")
def approve(platform: str, code: str, url: UrlOpt = None, as_json: JsonOpt = False) -> None:
    """Allowlist the chat that was shown CODE."""
    st = settings()
    name = _plugin(platform)
    res = _api(st, url, "POST", f"/api/plugins/{name}/pair", {"code": code})
    chat_id = res.get("chat_id") if res is not None else PairingStore(ensure_home(st.home), name).pair(code)
    if chat_id is None:
        err_console.print("[red]unknown or expired pairing code[/red]")
        raise typer.Exit(1)
    if want_json(as_json):
        dump_json({"platform": name, "chat_id": chat_id, "paired": True})
    else:
        console.print(f"paired chat {chat_id} with {name}")


@app.command("revoke")
def revoke(platform: str, chat_id: str, as_json: JsonOpt = False) -> None:
    """Remove CHAT_ID from the platform's allowlist (its next message gets a new code)."""
    st = settings()
    name = _plugin(platform)
    removed = PairingStore(ensure_home(st.home), name).revoke(chat_id)
    if want_json(as_json):
        dump_json({"platform": name, "chat_id": chat_id, "revoked": removed})
    elif removed:
        console.print(f"revoked chat {chat_id} from {name}")
    if not removed:
        err_console.print(f"[red]{chat_id} is not allowlisted for {name}[/red]")
        raise typer.Exit(1)
