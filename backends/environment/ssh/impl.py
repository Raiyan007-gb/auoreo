"""SSH environment (asyncssh, imported lazily; extra `ssh`).

`asyncssh.connect(host, port, username=, client_keys=[key], known_hosts=<file>,
connect_timeout=)` with:
- the private key imported from the secret store (`key_secret: "secret:<NAME>"`) — never
  from `~/.ssh`, an agent or any file a tool could read (`agent_path=None`, `config=None`);
- `known_hosts` required: there is no `known_hosts=None` bypass; a mismatch fails closed;
- publickey auth only.

One connection per backend instance (reopened if it drops); each command is a channel
running `bash -lc <cmd>` (with `term_type` when tty). The host's own environment applies:
the local child env is never shipped to the remote side.

With `settings.m7.ssh.hosts` on: the key is read as `SecretStore.candidates` (the current
value, then `<NAME>#prev` inside its rotation grace window; the next one is tried only after
a key-import or authentication failure), and every socket open calls `net_audit` (attached
by the environment pool or the CLI; a no-op when absent). `keepalive_interval_s` is passed
as asyncssh `keepalive_interval` when set.
"""

from __future__ import annotations

import asyncio
import contextlib
import shlex
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.environment import EnvironmentBackend, ExecRequest
from auoreo.core.secrets import SecretStore
from auoreo.tools.env_proc import Chunk, ProcTable, StreamProc
from auoreo.tools.shell_sessions import ShellError

REMEDY = 'set settings.environment_ssh {host, username, key_secret: "secret:<NAME>", known_hosts}'


def _asyncssh() -> Any:
    import asyncssh

    return asyncssh


def remote_command(req: ExecRequest) -> str:
    cmd = f"bash -lc {shlex.quote(req.cmd)}"
    if req.workdir:
        return f"cd {shlex.quote(req.workdir)} && exec {cmd}"
    return cmd


class Backend(EnvironmentBackend):
    name = "ssh"
    remote = True

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._conn: Any = None
        self.net_audit: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        """Called with `{mode, host, port}` after every socket open (M7 `net.ssh` audit)."""
        self.host_ext: Any = None
        """M7 v2 `core.hosts.HostRuntime` of a host file (tags, audit mode, jump hop), set by
        the environment pool after construction; None for settings entries."""
        self.key_override: list[str] | None = None
        """Operator CLI (`auoreo ssh deploy|rotate`): the exact key texts to offer instead of
        the stored secret, to prove a login with a new key before it is stored. Never set
        by a tool or the environment pool."""
        self._jump: Any = None
        self._lock = asyncio.Lock()
        self.table = ProcTable(self.name, int(getattr(settings, "shell_max_processes", 8) or 8))

    @property
    def cfg(self) -> Any:
        return getattr(self.settings, "environment_ssh", None)

    def _problems(self) -> list[str]:
        cfg = self.cfg
        if cfg is None:
            return ["settings.environment_ssh is not set"]
        out = []
        if cfg.known_hosts is None:
            out.append("environment_ssh.known_hosts is required (no host-key bypass)")
        elif not cfg.known_hosts.expanduser().is_file():
            out.append(f"known_hosts file not found: {cfg.known_hosts}")
        if not cfg.key_secret:
            out.append('environment_ssh.key_secret is required ("secret:<NAME>" in the secret store)')
        elif not self._keys(cfg.key_secret):
            out.append(f"secret {cfg.key_secret.removeprefix('secret:')} is not set")
        if (hop := self.hop) is not None and not self._store_keys(hop.key_secret):
            out.append(f"jump host {hop.name}: secret {hop.key_secret.removeprefix('secret:')} is not set")
        return out

    def _keys(self, ref: str) -> list[str]:
        """Key texts to offer, in order. Never logged, returned to a tool or put in an error."""
        if self.key_override is not None:  # operator CLI only (`core/host_keys.py`)
            return list(self.key_override)
        store = SecretStore(self.settings.home, getattr(self.settings, "keyring", "allow-file"))
        name = ref.removeprefix("secret:")
        if getattr(getattr(getattr(self.settings, "m7", None), "ssh", None), "hosts", False):
            return store.candidates(name, by="backend:ssh")
        value = store.get(name)
        return [value] if value else []

    @property
    def target(self) -> str:
        cfg = self.cfg
        return f"{cfg.username}@{cfg.host}:{cfg.port}" if cfg is not None else "?"

    def _store_keys(self, ref: str) -> list[str]:
        """The jump hop's own keys: always from the store, never `key_override`."""
        store = SecretStore(self.settings.home, getattr(self.settings, "keyring", "allow-file"))
        return store.candidates(ref.removeprefix("secret:"), by="backend:ssh")

    @property
    def hop(self) -> Any:
        """The M7 jump hop (`core.hosts.JumpHop`) or None."""
        return getattr(self.host_ext, "jump", None)

    async def _connect(self) -> Any:
        asyncssh = _asyncssh()
        if problems := self._problems():
            raise ShellError(f"ssh environment not configured: {'; '.join(problems)}")
        cfg = self.cfg
        tunnel = await self.jump_connection()
        detail: dict[str, Any] = {"mode": "ssh", "host": cfg.host, "port": cfg.port}
        if self.hop is not None:
            detail.update(role="target", via=self.hop.name)
        return await self._login(asyncssh, cfg, self._keys(cfg.key_secret), self.target, detail, tunnel)

    async def jump_connection(self) -> Any:
        """The authenticated connection to the jump host (opened once, reopened if it
        dropped), or None without one. It uses the jump's own key, known_hosts entry and
        keepalive; the target's key is never offered to the bastion."""
        hop = self.hop
        if hop is None:
            return None
        if self._jump is not None and not self._jump.is_closed():
            return self._jump
        cfg = self.cfg
        jcfg = SimpleNamespace(
            host=hop.host, port=hop.port, username=hop.user, key_secret=hop.key_secret,
            known_hosts=cfg.known_hosts, connect_timeout_s=cfg.connect_timeout_s,
            keepalive_interval_s=hop.keepalive_interval_s,
        )  # fmt: skip
        label = f"{hop.user}@{hop.host}:{hop.port} (jump host {hop.name})"
        detail = {"name": hop.name, "mode": "ssh", "host": hop.host, "port": hop.port, "role": "jump"}
        self._jump = await self._login(
            _asyncssh(), jcfg, self._store_keys(hop.key_secret), label, detail, None
        )
        return self._jump

    async def _login(
        self, asyncssh: Any, cfg: Any, keys: list[str], label: str, detail: dict[str, Any], tunnel: Any
    ) -> Any:
        failure = ShellError(f"ssh {label}: secret {cfg.key_secret.removeprefix('secret:')} is not set")
        for key_text in keys:
            try:
                key = asyncssh.import_private_key(key_text)
            except (asyncssh.KeyImportError, ValueError) as exc:
                failure = ShellError(f"ssh key {cfg.key_secret} is not a valid private key: {exc}")
                continue
            try:
                conn = await self._open(asyncssh, cfg, key, label, tunnel)
            except asyncssh.PermissionDenied:
                failure = ShellError(f"ssh {label}: authentication failed (key {cfg.key_secret} rejected)")
                continue
            if self.net_audit is not None:
                await self.net_audit(detail)
            return conn
        raise failure from None

    async def _open(self, asyncssh: Any, cfg: Any, key: Any, label: str = "", tunnel: Any = None) -> Any:
        label = label or self.target
        extra: dict[str, Any] = {}
        if getattr(cfg, "keepalive_interval_s", None) is not None:
            extra["keepalive_interval"] = cfg.keepalive_interval_s
        if tunnel is not None:  # an open SSHClientConnection; never a string (that reads config)
            extra["tunnel"] = tunnel
        try:
            return await asyncssh.connect(
                cfg.host, cfg.port, username=cfg.username, client_keys=[key],
                known_hosts=str(cfg.known_hosts.expanduser()), connect_timeout=cfg.connect_timeout_s,
                agent_path=None, config=None, preferred_auth="publickey", password=None, **extra,
            )  # fmt: skip
        except asyncssh.HostKeyNotVerifiable as exc:
            raise ShellError(
                f"ssh {label}: host key does not match {cfg.known_hosts} — refusing to connect "
                f"(fail closed; verify the host and update known_hosts): {exc.reason}"
            ) from None
        except TimeoutError:
            raise ShellError(f"ssh {label}: connect timed out after {cfg.connect_timeout_s:g}s") from None
        except asyncssh.PermissionDenied:
            raise
        except (OSError, asyncssh.Error) as exc:
            raise ShellError(f"ssh {label}: connection failed: {exc}") from None

    async def _connection(self) -> Any:
        async with self._lock:
            if self._conn is None or self._conn.is_closed():
                self._conn = await self._connect()
            return self._conn

    async def check(self) -> BackendStatus:
        try:
            _asyncssh()
        except ImportError:
            return BackendStatus(False, "asyncssh not installed", "uv sync --extra ssh")
        if problems := self._problems():
            return BackendStatus(False, "; ".join(problems), REMEDY)
        try:
            await self._connection()
        except ShellError as exc:
            return BackendStatus(False, str(exc), REMEDY)
        return BackendStatus(True, f"connected to {self.target} (host key verified)")

    async def exec_command(self, req: ExecRequest, *, session_id: str) -> Chunk:
        asyncssh = _asyncssh()
        conn = await self._connection()
        proc = self.table.new(session_id, req.cmd, tty=req.tty)
        try:
            kwargs: dict[str, Any] = {"encoding": None}
            if req.tty:
                kwargs.update(term_type="dumb", term_size=(200, 50))
            else:
                kwargs["stderr"] = asyncssh.STDOUT
            chan = await conn.create_process(remote_command(req), **kwargs)
        except BaseException:
            self.table.procs.pop(proc.id, None)
            raise

        async def write(chars: str) -> None:
            chan.stdin.write(chars.encode())
            await chan.stdin.drain()

        async def kill() -> None:
            with contextlib.suppress(Exception):
                chan.kill()
            chan.close()

        proc.writer, proc.killer = write, kill
        proc.spawn(self._pump(proc, chan))
        return await self.table.collect(proc, req.yield_time_ms)

    @staticmethod
    async def _pump(proc: StreamProc, chan: Any) -> None:
        while data := await chan.stdout.read(65536):
            proc.feed(data)
        result = await chan.wait(check=False)
        code = result.exit_status
        if code is None or code < 0:
            code = -1 if result.exit_signal is None else 128 + 9
        proc.finish(code)

    async def write_stdin(self, process_id: str, chars: str, *, yield_time_ms: int = 1_000) -> Chunk:
        return await self.table.write(process_id, chars, yield_time_ms)

    async def upload(self, local_path: str, remote_path: str) -> None:
        conn = await self._connection()
        async with conn.start_sftp_client() as sftp:
            await sftp.put(local_path, remote_path)

    async def download(self, remote_path: str, local_path: str) -> None:
        conn = await self._connection()
        async with conn.start_sftp_client() as sftp:
            await sftp.get(remote_path, local_path)

    async def close_session(self, session_id: str) -> None:
        await self.table.close_session(session_id)

    async def close(self) -> None:
        await self.table.close_all()
        for conn in (self._conn, self._jump):  # the target first, then its tunnel
            if conn is not None:
                conn.close()
                with contextlib.suppress(Exception):
                    await conn.wait_closed()
        self._conn = self._jump = None
