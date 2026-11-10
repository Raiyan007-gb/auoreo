"""`auoreo ssh add|list|test` (M7 hosts-a): the SSH host inventory under `<home>/hosts/`.

- `add` writes `hosts/<name>.yaml`. `--key` is the NAME of a stored secret
  (`auoreo secrets set <NAME>`); the file never holds key material.
- `list` reads the host files only (no network).
- `test` is the only place a server host key becomes trusted (trust-on-first-use): it shows
  the SHA-256 fingerprint and asks, or checks `--fingerprint` when there is no terminal. A
  key that differs from `<home>/ssh/known_hosts` is an error (exit 3), never a prompt, unless
  the operator passes `--replace-key`. Every trust decision is audited (`host.trust`).

The commands work with `settings.m7.ssh.hosts` off: they only manage files. The agent can use
a host only when the flag is on.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from auoreo.cli.common import dump_json, settings
from auoreo.core import hosts
from auoreo.core.settings import Settings

app = typer.Typer(no_args_is_help=True)

EXIT_INVALID, EXIT_CONNECT, EXIT_KEY = 2, 1, 3
PROBE = "echo auoreo-ssh-ok"
Record = Callable[..., Awaitable[None]]
Confirm = Callable[[str], bool]


def _fail(message: str, code: int) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code)


@app.command("add")
def add(
    name: str,
    host: Annotated[str, typer.Option("--host", help="Hostname or IP address.")],
    user: Annotated[str, typer.Option("--user", help="Remote user.")],
    key: Annotated[str, typer.Option("--key", help="NAME of the stored secret holding the private key.")],
    port: Annotated[int, typer.Option("--port")] = 22,
    keepalive: Annotated[float, typer.Option("--keepalive", help="Seconds; 0 disables.")] = 30,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing host file.")] = False,
) -> None:
    """Write `<home>/hosts/NAME.yaml`. Then run `auoreo ssh test NAME` to trust its host key."""
    fields = {
        "host": host,
        "port": port,
        "user": user,
        "key_secret": f"secret:{key.removeprefix('secret:')}",
        "keepalive_interval_s": keepalive,
    }
    try:
        path = hosts.write_host(settings().home, name, fields, force=force)
    except (ValueError, FileExistsError) as exc:  # the message never echoes --key
        raise _fail(f"not added: {exc}", EXIT_INVALID) from None
    typer.echo(str(path))


@app.command("list")
def list_(as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False) -> None:
    """List hosts: name, address, user, key secret NAME, and whether a host key is trusted."""
    home = settings().home
    found, problems = hosts.read_hosts(home)
    rows = [
        {
            "name": m.name,
            "host": m.host,
            "port": m.port,
            "user": m.user,
            "key": m.key_secret.removeprefix("secret:"),
            "trusted": bool(hosts.known_keys(home, m.host, m.port)),
        }
        for m in found.values()
    ]
    if as_json:
        dump_json(rows)
    else:
        for r in rows:
            mark = "yes" if r["trusted"] else "no"
            typer.echo(f"{r['name']}  {r['host']}:{r['port']}  {r['user']}  key={r['key']}  trusted={mark}")
        if not rows:
            typer.echo("no hosts (add one with `auoreo ssh add`)")
    for line in problems:
        typer.echo(f"invalid: {line}", err=True)


async def _decide(
    home: Path, name: str, m: Any, probe: hosts.ProbeResult, *, replace_key: bool,
    fingerprint: str | None, confirm: Confirm | None, echo: Callable[[str], None], record: Record,
) -> int:  # fmt: skip
    """Trust decision for a key that is not (or no longer) in known_hosts; 0 = trusted now."""
    known = hosts.known_keys(home, m.host, m.port)
    path = hosts.known_hosts_path(home)
    if known and not replace_key:
        old = ", ".join(hosts.fingerprint_of(k) for k in known)
        echo(
            f"HOST KEY CHANGED for {name} ({m.host}:{m.port}): {path} has {old}, the server presents "
            f"{probe.fingerprint_sha256}. Not connecting. If the host was rebuilt, verify the new "
            f"fingerprint out of band and run `auoreo ssh test {name} --replace-key`."
        )
        return EXIT_KEY
    for k in known:
        echo(f"replacing {hosts.fingerprint_of(k)}")
    if fingerprint is not None:
        if not hmac.compare_digest(fingerprint.strip(), probe.fingerprint_sha256):
            echo(f"--fingerprint does not match the presented key {probe.fingerprint_sha256}; not trusted")
            return EXIT_KEY
    elif confirm is None:
        echo("no terminal to confirm on: pass --fingerprint SHA256:... to trust this key")
        return EXIT_KEY
    elif not confirm(f"Trust this host key for {name} ({m.host}:{m.port})?"):
        echo("not trusted")
        return EXIT_KEY
    hosts.trust(home, m.host, m.port, probe.openssh_line, replace=bool(known))
    await record(
        actor="operator",
        action="host.trust",
        decision="allow",
        reason="fingerprint" if fingerprint is not None else "confirmed",
        detail={
            "name": name,
            "host": m.host,
            "port": m.port,
            "key_type": probe.key_type,
            "fingerprint": probe.fingerprint_sha256,
            "replaced": bool(known),
        },
    )
    echo(f"trusted {probe.fingerprint_sha256} in {path}")
    return 0


async def run_test(
    st: Settings,
    name: str,
    *,
    record: Record,
    harness: Any = None,
    replace_key: bool = False,
    fingerprint: str | None = None,
    confirm: Confirm | None = None,
    echo: Callable[[str], None] = typer.echo,
    timeout: float = 10.0,
) -> int:
    """`auoreo ssh test NAME`; returns the exit code. `confirm` is None without a terminal."""
    from auoreo.backends.environment import ExecRequest
    from auoreo.tools import ssh_gate
    from auoreo.tools.environments import KIND, apply_overrides
    from auoreo.tools.shell_sessions import ShellError

    home = Path(st.home)
    m = hosts.read_hosts(home)[0].get(name)
    if m is None:
        echo(f"no host '{name}' in {hosts.hosts_dir(home)} (see `auoreo ssh list`)")
        return EXIT_INVALID
    path = hosts.known_hosts_path(home)
    if not path.is_file():  # the backend requires the file; an empty one trusts nothing
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(mode=0o600)
    try:
        probe = await hosts.probe_host_key(m.host, m.port, timeout)
    except (OSError, TimeoutError, ValueError) as exc:
        echo(f"cannot reach {name} ({m.host}:{m.port}): {type(exc).__name__}: {exc}"[:300])
        return EXIT_CONNECT
    except Exception as exc:  # asyncssh protocol errors
        echo(f"no host key from {name} ({m.host}:{m.port}): {type(exc).__name__}: {exc}"[:300])
        return EXIT_CONNECT
    echo(f"{name} ({m.host}:{m.port}) host key {probe.key_type} {probe.fingerprint_sha256}")
    if probe.openssh_line not in hosts.known_keys(home, m.host, m.port):
        code = await _decide(
            home, name, m, probe, replace_key=replace_key, fingerprint=fingerprint,
            confirm=confirm, echo=echo, record=record,
        )  # fmt: skip
        if code:
            return code
    if harness is None:
        from auoreo.core.paths import bundled_root
        from auoreo.harness.discovery import default_roots
        from auoreo.harness.registry import Harness

        harness = Harness(default_roots(home, Path.cwd(), bundled_root(), st.extra_roots))
    bdef = harness.select_backend(KIND, "ssh")
    if bdef is None:
        echo("the ssh environment backend is not available")
        return EXIT_CONNECT
    backend = bdef.load()(apply_overrides(st, hosts.manifest_to_override(m, home)))
    ssh_gate.attach(backend, None, record, name, actor="operator", always=True)
    try:
        status = await backend.check()
        if not status.ok:
            echo(f"failed: {status.detail}")
            return EXIT_KEY if "host key does not match" in status.detail else EXIT_CONNECT
        chunk = await backend.exec_command(
            ExecRequest(PROBE, yield_time_ms=int(timeout * 1000)), session_id="cli"
        )
        if chunk.exit_code != 0 or "auoreo-ssh-ok" not in chunk.output:
            echo(f"connected, but the test command failed (exit {chunk.exit_code}; is bash installed?)")
            return EXIT_CONNECT
    except ShellError as exc:
        echo(f"failed: {exc}")
        return EXIT_CONNECT
    finally:
        await backend.close()
    echo(f"ok: {status.detail}; command round trip passed")
    return 0


@contextlib.asynccontextmanager
async def _audit(st: Settings) -> AsyncIterator[Record]:
    """The audit log of `state.db`, opened the way the server opens it."""
    from auoreo.core.audit import AuditLog
    from auoreo.core.db import Database

    st.db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(st.db_path, encryption=st.db_encryption)
    await db.init()
    try:
        yield AuditLog(db).record
    finally:
        await db.close()


def _tty_confirm() -> Confirm | None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    return lambda question: typer.confirm(question, default=False)


@app.command("test")
def test(
    name: str,
    replace_key: Annotated[
        bool, typer.Option("--replace-key", help="Accept a host key that differs from known_hosts.")
    ] = False,
    fingerprint: Annotated[
        str | None, typer.Option("--fingerprint", help="SHA256:... to trust without a prompt.")
    ] = None,
) -> None:
    """Show the server's host-key fingerprint, trust it on first use, then run a test command."""
    st = settings()

    async def go() -> int:
        async with _audit(st) as record:
            return await run_test(
                st, name, record=record, replace_key=replace_key, fingerprint=fingerprint,
                confirm=_tty_confirm(),
            )  # fmt: skip

    code = asyncio.run(go())
    if code:
        raise typer.Exit(code)
