"""G5a: `auoreo session create|get|rename|unarchive|queue|steer|share|retry|revert|attach`.

Each command talks to a running server when there is one (`--url`, the daemon socket, or
`http://<host>:<port>`), else works in-process on `state.db`. `queue`, `steer` and `attach`
act on a *live* session and therefore need the server."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

import httpx
import typer

from auoreo.cli import target
from auoreo.cli.commands.session import _run_op, app
from auoreo.cli.common import console, dump_json, err_console, settings
from auoreo.core import sessions_more as more
from auoreo.core import sessions_ops as ops
from auoreo.core.services import Services

UrlOpt = Annotated[
    str | None, typer.Option("--url", envvar="AUOREO_URL", help="Server URL (default: discover one).")
]
JsonOpt = Annotated[bool, typer.Option("--json")]


def _print_detail(d: dict[str, Any], as_json: bool) -> None:
    if as_json:
        dump_json(d)
        return
    for key in ("id", "title", "agent", "workflow", "model", "status", "parent_id", "archived", "updated_at"):
        console.print(f"{key:10} {d.get(key)}")


def _local_bad_input[T](fn: Callable[[Services], Awaitable[T]]) -> T:
    try:
        return _run_op(fn)
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


@app.command("create")
def create(
    title: Annotated[str | None, typer.Option(help="Session title.")] = None,
    agent: Annotated[str | None, typer.Option(help="Agent.")] = None,
    workflow: Annotated[str | None, typer.Option(help="Workflow.")] = None,
    model: Annotated[str | None, typer.Option("--model", "-m")] = None,
    url: UrlOpt = None,
) -> None:
    """Create an empty session and print its id."""
    body = {
        k: v for k, v in {"title": title, "agent": agent, "workflow": workflow, "model": model}.items() if v
    }
    if base := target.discover(url):
        print(target.call(base, "POST", "/api/sessions", json=body)["id"])
        return

    async def go(svc: Services) -> str:
        return (await svc.create_session(**body)).id

    print(_run_op(go))


@app.command("get")
def get(session_id: str, url: UrlOpt = None, as_json: JsonOpt = False) -> None:
    """Show one session (lineage, archive state)."""
    if base := target.discover(url):
        _print_detail(target.call(base, "GET", f"/api/sessions/{session_id}"), as_json)
        return

    async def go(svc: Services) -> dict[str, Any]:
        return ops.session_info(await ops._require(svc, session_id))

    _print_detail(_run_op(go), as_json)


@app.command("rename")
def rename(session_id: str, title: str, url: UrlOpt = None) -> None:
    """Set a session's title."""
    if base := target.discover(url):
        target.call(base, "PATCH", f"/api/sessions/{session_id}", json={"title": title})
    else:

        async def go(svc: Services) -> None:
            await more.rename_session(svc, session_id, title)

        _local_bad_input(go)
    console.print(f"renamed {session_id}")


@app.command("unarchive")
def unarchive(session_id: str, url: UrlOpt = None) -> None:
    """Show an archived session in `list` again."""
    if base := target.discover(url):
        target.call(base, "POST", f"/api/sessions/{session_id}/unarchive")
    else:

        async def go(svc: Services) -> None:
            await ops.set_archived(svc, session_id, False)

        _run_op(go)
    console.print(f"unarchived {session_id}")


def _send(session_id: str, message: str, mode: str, url: str | None) -> None:
    base = target.require(url)
    body = {"message": message, "session_id": session_id, "mode": mode}
    accepted = target.call(base, "POST", "/api/chat", json=body)
    console.print(
        f"{mode} → {session_id}" + (f" (run {accepted['run_id']})" if accepted.get("run_id") else "")
    )


@app.command("queue")
def queue(session_id: str, message: str, url: UrlOpt = None) -> None:
    """Queue a follow-up turn on a live session (runs after the current one)."""
    _send(session_id, message, "followup", url)


@app.command("steer")
def steer(session_id: str, message: str, url: UrlOpt = None) -> None:
    """Inject guidance into the running turn's next step (a new turn when idle)."""
    _send(session_id, message, "steer", url)


@app.command("share")
def share(
    session_id: str,
    revoke: Annotated[bool, typer.Option("--revoke", help="Revoke every share link instead.")] = False,
    url: UrlOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Mint a read-only share link (token shown once), or revoke all of them."""
    base = target.discover(url)
    if revoke:
        if base:
            n = target.call(base, "DELETE", f"/api/sessions/{session_id}/share")["revoked"]
        else:

            async def gone(svc: Services) -> int:
                return await more.revoke_shares(svc, session_id)

            n = _run_op(gone)
        console.print(f"revoked {n} share link(s) for {session_id}")
        return
    if base:
        out = target.call(base, "POST", f"/api/sessions/{session_id}/share")
    else:

        async def mint(svc: Services) -> str:
            return await more.create_share(svc, session_id)

        token = _run_op(mint)
        path = f"/api/shared/{token}"
        st = settings()
        out = {"session_id": session_id, "token": token, "path": path}
        out["url"] = (st.cli.share_base_url or target.local_url(st)).rstrip("/") + path
    if as_json:
        dump_json(out)
    else:
        print(out["url"])
        err_console.print("[dim]read-only link; anyone holding it can read this session[/dim]")


@app.command("revert")
def revert(session_id: str, to_idx: int, url: UrlOpt = None) -> None:
    """Keep messages 0..TO_IDX (-1 clears); the cut is recorded as a `session.reverted` event."""
    if base := target.discover(url):
        n = target.call(base, "POST", f"/api/sessions/{session_id}/revert", json={"to_idx": to_idx})[
            "removed"
        ]
    else:

        async def go(svc: Services) -> int:
            return await more.revert_session(svc, session_id, to_idx)

        n = _local_bad_input(go)
    console.print(f"reverted {session_id}: {n} message(s) cut")


@app.command("retry")
def retry(session_id: str, url: UrlOpt = None) -> None:
    """Re-run the last user turn (the old answer is cut and recorded)."""
    if base := target.discover(url):
        out = target.call(base, "POST", f"/api/sessions/{session_id}/retry")
        console.print(f"retrying {session_id} (run {out['run_id']}); follow with `auoreo session attach`")
        return

    async def go(svc: Services) -> str:
        run_id, _text = await more.retry_session(svc, session_id)
        await svc.coordinator.wait(session_id, None)
        await svc.coordinator.background_idle(10)
        result = svc.coordinator.result(run_id) if run_id else None
        return result.status if result else "completed"

    status: str = _local_bad_input(go)
    console.print(f"retried {session_id}: {status}")
    if status not in {"completed", "max_steps"}:
        raise typer.Exit(1)


@app.command("attach")
def attach(
    session_id: str,
    url: UrlOpt = None,
    replay: Annotated[bool, typer.Option("--replay", help="Print the session's past events first.")] = False,
    until_idle: Annotated[bool, typer.Option("--until-idle", help="Exit after the next run ends.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="One JSON event per line.")] = False,
) -> None:
    """Stream a live session's events to the terminal (Ctrl-C detaches; the session keeps going)."""
    from auoreo.cli.commands.run import _pretty
    from auoreo.remote.cli_client import TERMINAL, _sse

    base = target.require(url)
    try:
        with target.client_for(base, timeout=None) as c:
            if c.get(f"/api/sessions/{session_id}").status_code == 404:
                err_console.print(f"[red]unknown session '{session_id}'[/red]")
                raise typer.Exit(1)
            cursor = 0 if replay else c.get("/api/events/latest").raise_for_status().json()["seq"]
            err_console.print(f"[dim]attached to {session_id} via {base} (Ctrl-C to detach)[/dim]")
            params = {"session_id": session_id, "after_seq": cursor}
            with c.stream("GET", "/api/events", params=params) as resp:
                for ev in _sse(resp):
                    if as_json:
                        print(json.dumps(ev), flush=True)
                    else:
                        _pretty(ev["type"], ev.get("data") or {})
                    if until_idle and ev["type"] in TERMINAL and ev.get("seq", 0) > cursor:
                        break
    except KeyboardInterrupt:
        err_console.print("\n[dim]detached[/dim]")
    except httpx.HTTPError as exc:
        err_console.print(f"[red]{base}: {exc}[/red]")
        raise typer.Exit(1) from exc
