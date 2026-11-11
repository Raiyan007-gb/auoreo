"""`auoreo ssh keygen|pubkey|deploy|rotate|rm` (M7 hosts-b, D5): the key lifecycle of the
host inventory. Operator commands only: no tool, chat app or web route runs them, and a bash
floor refuses them in tool shells (`core/floors_ssh.py`).

- `keygen` generates an ed25519 key in process, stores the private half as a secret (default
  `SSH_KEY_<NAME>`), points the host file at it and prints the public line and fingerprint.
- `deploy` appends the public line remotely (connecting with the host's current key or with
  `--via SECRET` for a first install) and then requires a login with the new key alone.
- `rotate` never swaps the secret before every host using it has logged in with the new key;
  the old value stays as `<SECRET>#prev` for the grace window (`m7.ssh.rotate_grace_h`).
- `rm` deletes the host file (`--forget-key` also drops its `known_hosts` entry); never the secret.

Every step is audited (`host.key.*`, `host.deleted`) with names and fingerprints only.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer

from auoreo.cli.commands.ssh import EXIT_CONNECT, EXIT_INVALID, Record, _audit, app
from auoreo.cli.common import settings
from auoreo.core import host_keys as hk
from auoreo.core import hosts
from auoreo.core.secrets import SecretStore
from auoreo.core.settings import Settings
from auoreo.harness.manifests_hosts import HostManifest

Echo = Callable[[str], None]


@dataclass
class Ops:
    """What the key commands need; tests build one around an in-process server."""

    st: Settings
    record: Record
    echo: Echo = typer.echo
    harness: Any = None

    @property
    def home(self) -> Path:
        return Path(self.st.home)

    @property
    def store(self) -> SecretStore:
        return SecretStore(self.home, getattr(self.st, "keyring", "allow-file"))

    def found(self) -> dict[str, HostManifest]:
        return hosts.read_hosts(self.home)[0]

    def backend(self, m: HostManifest, keys: list[str]) -> Any:
        """An ssh backend for host file `m` offering exactly `keys` (its jump hop, if any,
        uses the jump's own stored key)."""
        from auoreo.tools import ssh_gate
        from auoreo.tools.environments import KIND, apply_overrides

        harness = self.harness
        if harness is None:
            from auoreo.core.paths import bundled_root
            from auoreo.harness.discovery import default_roots
            from auoreo.harness.registry import Harness

            harness = Harness(default_roots(self.home, Path.cwd(), bundled_root(), self.st.extra_roots))
        bdef = harness.select_backend(KIND, "ssh")
        if bdef is None:
            raise RuntimeError("the ssh environment backend is not available")
        b = bdef.load()(apply_overrides(self.st, hosts.manifest_to_override(m, self.home)))
        ext = hosts.runtime_for(self.found(), m)
        if ext.problem:
            raise RuntimeError(ext.problem)
        b.host_ext, b.key_override = ext, keys
        ssh_gate.attach(b, None, self.record, m.name, actor="operator", always=True)
        return b

    async def run(self, m: HostManifest, keys: list[str], cmd: str) -> tuple[bool, str]:
        """`(ok, detail)` of one remote command; never raises for a connection problem."""
        from auoreo.backends.environment import ExecRequest

        try:
            b = self.backend(m, keys)
        except RuntimeError as exc:
            return False, str(exc)
        try:
            status = await b.check()
            if not status.ok:
                return False, status.detail
            chunk = await b.exec_command(ExecRequest(cmd, yield_time_ms=15_000), session_id="cli")
            if chunk.exit_code != 0:
                return False, f"remote command failed (exit {chunk.exit_code})"
            return True, chunk.output
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"[:300]
        finally:
            await b.close()

    async def install(self, m: HostManifest, connect: list[str], kp: hk.KeyPair, secret: str) -> str | None:
        """Append `kp` remotely, then prove a login with it alone. None = done, else why not."""
        ok, why = await self.run(m, connect, hk.install_command(hk.authorized_line(kp, secret)))
        if not ok:
            return f"{m.name}: could not install the key: {why}"
        ok, out = await self.run(m, [kp.private], hk.PROBE)
        if not ok or "auoreo-ssh-ok" not in out:
            return f"{m.name}: the new key was installed but a login with it alone failed: {out[:200]}"
        return None


def _host(ops: Ops, name: str) -> HostManifest | None:
    m = ops.found().get(name)
    if m is None:
        ops.echo(f"no host '{name}' in {hosts.hosts_dir(ops.home)} (see `auoreo ssh list`)")
    return m


def _secret(m: HostManifest) -> str:
    return m.key_secret.removeprefix("secret:")


# ------------------------------------------------------------------ operations
async def keygen(ops: Ops, name: str, secret: str | None = None) -> int:
    m = _host(ops, name)
    if m is None:
        return EXIT_INVALID
    try:
        secret = hk.check_secret(secret or hk.default_secret(name))
    except ValueError as exc:
        ops.echo(f"not generated: {exc}")
        return EXIT_INVALID
    if ops.store.get(secret):
        ops.echo(f"secret {secret} already exists; pick another --secret or use `auoreo ssh rotate {name}`")
        return EXIT_INVALID
    kp = hk.generate()
    ops.store.set(secret, kp.private)
    hosts.write_host(
        ops.home, name, {**m.model_dump(exclude={"name"}), "key_secret": f"secret:{secret}"}, force=True
    )
    hk.record_key(ops.home, secret, fingerprint=kp.fingerprint, created_at=hk.now())
    await ops.record(
        actor="operator", action="host.key.generated", decision="allow",
        detail={"name": name, "secret": secret, "alg": hk.ALG, "fingerprint": kp.fingerprint},
    )  # fmt: skip
    ops.echo(hk.authorized_line(kp, secret))
    ops.echo(
        f"{kp.fingerprint} stored as secret {secret}; next: `auoreo ssh deploy {name} --via <OLD_SECRET>`"
    )
    return 0


async def pubkey(ops: Ops, name: str) -> int:
    m = _host(ops, name)
    if m is None:
        return EXIT_INVALID
    secret = _secret(m)
    try:
        kp = hk.from_private(ops.store.get(secret), secret=secret)
    except ValueError as exc:
        ops.echo(str(exc))
        return EXIT_INVALID
    ops.echo(hk.authorized_line(kp, secret))
    ops.echo(kp.fingerprint)
    return 0


async def deploy(ops: Ops, name: str, via: str | None = None) -> int:
    m = _host(ops, name)
    if m is None:
        return EXIT_INVALID
    secret = _secret(m)
    try:
        kp = hk.from_private(ops.store.get(secret), secret=secret)
        via_name = hk.check_secret(via) if via else None
    except ValueError as exc:
        ops.echo(f"not deployed: {exc}")
        return EXIT_INVALID
    via_value = ops.store.get(via_name) if via_name else None
    connect = [via_value] if via_value else ops.store.candidates(secret)
    if via_name and not via_value:
        ops.echo(f"not deployed: secret {via_name} is not set")
        return EXIT_INVALID
    why = await ops.install(m, connect, kp, secret)
    await ops.record(
        actor="operator", action="host.key.deployed", decision="deny" if why else "allow",
        reason=(why or "")[:300],
        detail={"name": name, "host": m.host, "port": m.port, "secret": secret,
                "fingerprint": kp.fingerprint, "via": via_name, "verified": why is None},
    )  # fmt: skip
    if why:
        ops.echo(why)
        return EXIT_CONNECT
    hk.record_key(ops.home, secret, fingerprint=kp.fingerprint, deployed=[name])
    ops.echo(f"deployed {kp.fingerprint} to {name} ({m.host}:{m.port}); login with the new key verified")
    return 0


async def rotate(
    ops: Ops,
    name: str,
    *,
    grace_h: float | None = None,
    revoke_old: bool = False,
    skip: list[str] | None = None,
) -> int:
    m = _host(ops, name)
    if m is None:
        return EXIT_INVALID
    secret, skip = _secret(m), list(skip or [])
    try:
        old = hk.from_private(ops.store.get(secret), secret=secret)
    except ValueError as exc:
        ops.echo(f"not rotated: {exc}")
        return EXIT_INVALID
    users = sorted((h for h in ops.found().values() if h.key_secret == m.key_secret), key=lambda h: h.name)
    targets = [h for h in users if h.name not in skip]
    new = hk.generate()
    for h in targets:  # prove every host before anything changes locally
        if (why := await ops.install(h, [old.private], new, secret)) is not None:
            ops.echo(
                f"{why}\nnot rotated: secret {secret} is unchanged (hosts already updated keep both keys)"
            )
            await ops.record(
                actor="operator", action="host.key.rotated", decision="deny", reason=why[:300],
                detail={"secret": secret, "hosts": [t.name for t in targets], "skipped": skip,
                        "fingerprint": new.fingerprint, "prev_fingerprint": old.fingerprint, "grace_s": 0},
            )  # fmt: skip
            return EXIT_CONNECT
    hours = ops.st.m7.ssh.rotate_grace_h if grace_h is None else grace_h
    grace_s = 0 if revoke_old and not skip else int(hours * 3600)
    ops.store.rotate(secret, new.private, grace_s)
    t = hk.now()
    hk.record_key(
        ops.home, secret, fingerprint=new.fingerprint, rotated_at=t, prev_fingerprint=old.fingerprint,
        prev_expires=t + grace_s if grace_s else None, deployed=[h.name for h in targets],
    )  # fmt: skip
    done = [h.name for h in targets]
    detail: dict[str, Any] = {
        "secret": secret, "hosts": done, "skipped": skip, "fingerprint": new.fingerprint,
        "prev_fingerprint": old.fingerprint, "grace_s": grace_s,
    }  # fmt: skip
    await ops.record(actor="operator", action="host.key.rotated", decision="allow", detail=detail)
    ops.echo(f"rotated {secret}: {old.fingerprint} -> {new.fingerprint} on {', '.join(done) or 'no hosts'}")
    if skip:
        ops.echo(f"skipped {', '.join(skip)}: they keep working through {secret}#prev for {grace_s}s")
    if revoke_old:
        failed = [
            h.name for h in targets if not (await ops.run(h, [new.private], hk.remove_command(old.public)))[0]
        ]
        await ops.record(
            actor="operator", action="host.key.revoked", decision="deny" if failed else "allow",
            detail={**detail, "failed": failed},
        )  # fmt: skip
        if failed:
            ops.echo(f"the old key could not be removed on: {', '.join(failed)}")
            return EXIT_CONNECT
        ops.echo(f"removed {old.fingerprint} from {len(targets)} host(s)")
    return 0


async def remove(ops: Ops, name: str, *, forget_key: bool = False) -> int:
    m = _host(ops, name)
    if m is None:
        return EXIT_INVALID
    hosts.delete_host(ops.home, name)
    forgot = forget_key and hosts.forget_host_key(ops.home, m.host, m.port)
    await ops.record(
        actor="operator", action="host.deleted", decision="allow",
        detail={"name": name, "host": m.host, "port": m.port, "user": m.user, "key_name": _secret(m),
                "jump": m.jump, "tags": list(m.tags),
                "changed": ["file", *(["known_hosts"] if forgot else [])]},
    )  # fmt: skip
    ops.echo(
        f"removed {name}" + (" and its known_hosts entry" if forgot else "") + f" (secret {_secret(m)} kept)"
    )
    return 0


# ------------------------------------------------------------------ Typer commands
def _run(fn: Callable[..., Any], *args: Any, **kw: Any) -> None:
    st = settings()

    async def go() -> int:
        async with _audit(st) as record:
            return int(await fn(Ops(st, record), *args, **kw))

    if code := asyncio.run(go()):
        raise typer.Exit(code)


@app.command("keygen")
def keygen_cmd(
    name: str,
    secret: Annotated[
        str | None, typer.Option("--secret", help="Secret NAME (default SSH_KEY_<NAME>).")
    ] = None,
) -> None:
    """Generate an ed25519 key into the secret store; print its public line and fingerprint."""
    _run(keygen, name, secret)


@app.command("pubkey")
def pubkey_cmd(name: str) -> None:
    """Print the public key derived from the host's stored private key."""
    _run(pubkey, name)


@app.command("deploy")
def deploy_cmd(
    name: str,
    via: Annotated[
        str | None, typer.Option("--via", help="Secret NAME to log in with for a first install.")
    ] = None,
) -> None:
    """Append the host's public key to the remote authorized_keys and verify a login with it."""
    _run(deploy, name, via)


@app.command("rotate")
def rotate_cmd(
    name: str,
    grace: Annotated[float | None, typer.Option("--grace", help="Hours the old key keeps working.")] = None,
    revoke_old: Annotated[bool, typer.Option("--revoke-old", help="Remove the old key remotely.")] = False,
    skip: Annotated[
        list[str] | None, typer.Option("--skip", help="Host to leave on #prev (repeatable).")
    ] = None,
) -> None:
    """New key for the host's secret: installed and verified on every host using it, then swapped."""
    _run(rotate, name, grace_h=grace, revoke_old=revoke_old, skip=skip or [])


@app.command("rm")
def rm_cmd(
    name: str,
    forget_key: Annotated[
        bool, typer.Option("--forget-key", help="Also drop its known_hosts entry.")
    ] = False,
) -> None:
    """Delete the host file (the stored key is kept)."""
    _run(remove, name, forget_key=forget_key)
