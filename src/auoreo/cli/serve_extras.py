"""G5a: the optional parts of `auoreo serve` — WebSocket knobs, a tunnel started with the
server, mDNS advertisement — kept out of `commands/serve.py` (spec §18).

Everything here is honest about what is missing: an unknown tunnel provider, a missing
`cloudflared`/`tailscale` binary or an uninstalled `zeroconf` is a clear error, never a
silent no-op."""

from __future__ import annotations

import asyncio
import contextlib
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from auoreo import __version__
from auoreo.core import keyring_policy
from auoreo.core.settings import Settings
from auoreo.core.tunnels import PROVIDERS, TunnelError, TunnelInfo, TunnelManager

WS_AUTH_CHOICES = ("token", "off", "jwt", "capability")
TOKEN_WS_AUTH = frozenset({"jwt", "capability"})
"""Modes that only accept tokens minted from the server password (H1b)."""
TUNNEL_CHOICES = ("off", *sorted(PROVIDERS))
MDNS_TYPE = "_auoreo._tcp.local."
MDNS_INSTALL = "--mdns needs the `zeroconf` package: `uv pip install zeroconf` (or `pip install zeroconf`)"


class ServeOptionError(ValueError):
    """A flag combination `serve` refuses (exit 2 with this message)."""


@dataclass
class ServeExtras:
    """What the flags asked for, resolved against the settings before anything binds."""

    ws: bool = True
    tunnel: str = "off"
    mdns: bool = False
    tunnel_info: TunnelInfo | None = None
    _zc: Any = None
    _mdns_info: Any = None

    def uvicorn_kwargs(self, st: Settings) -> dict[str, Any]:
        """`ws="none"` makes uvicorn refuse upgrades; ping knobs are protocol-level."""
        if not self.ws:
            return {"ws": "none"}
        return {"ws_ping_interval": st.ws_ping_interval_s, "ws_ping_timeout": st.cli.ws_ping_timeout_s}

    def ws_line(self, host: str, port: int) -> str | None:
        if not self.ws:
            return None
        shown = f"[{host}]" if ":" in host else host
        return f"websocket on ws://{shown}:{port}/ws"

    # ------------------------------------------------------------- startup/shutdown
    async def started(self, st: Settings, port: int, emit: Callable[[str], None]) -> None:
        if self.tunnel != "off":
            mgr = TunnelManager(st.home, st.server_password)
            try:
                self.tunnel_info = await asyncio.to_thread(mgr.start, self.tunnel, port, 30.0)
            except TunnelError as exc:
                emit(f"tunnel failed: {exc}")
            else:
                emit(f"tunnel {self.tunnel_info.url}")
                if self.ws:
                    emit(f"tunnel websocket {self.tunnel_info.ws_url}")
                if self.tunnel == "cloudflared":
                    emit("note: cloudflared carries HTTP/WS signaling only; WebRTC media needs UDP/TURN")
        if self.mdns:
            try:
                await self._mdns_register(port)
                emit(f"mdns advertised as {MDNS_TYPE} on port {port}")
            except Exception as exc:  # zeroconf raises its own types; report, keep serving
                emit(f"mdns failed: {exc}")

    async def stopping(self, st: Settings) -> None:
        if self._zc is not None:
            with contextlib.suppress(Exception):
                await self._zc.async_unregister_service(self._mdns_info)
                await self._zc.async_close()
            self._zc = None
        if self.tunnel_info is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(TunnelManager(st.home, st.server_password).stop)
            self.tunnel_info = None

    async def _mdns_register(self, port: int) -> None:
        from zeroconf import ServiceInfo
        from zeroconf.asyncio import AsyncZeroconf

        name = socket.gethostname().split(".")[0] or "auoreo"
        info = ServiceInfo(
            MDNS_TYPE,
            f"auoreo-{name}.{MDNS_TYPE}",
            addresses=[socket.inet_aton(ip) for ip in lan_ipv4s()],
            port=port,
            properties={"version": __version__, "ws": "/ws" if self.ws else "", "auth": "token"},
            server=f"{name}.local.",
        )
        self._zc = AsyncZeroconf()
        self._mdns_info = info
        await self._zc.async_register_service(info)


def lan_ipv4s() -> list[str]:
    """Non-loopback IPv4 addresses of this host (best effort; may be empty)."""
    out: set[str] = set()
    with contextlib.suppress(OSError):
        out |= {ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if not ip.startswith("127.")}
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))  # TEST-NET: a UDP connect sends no packet
        ip = s.getsockname()[0]
        if not ip.startswith("127."):
            out.add(ip)
    return sorted(out)


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def lan_lines(host: str, port: int, ips: list[str] | None = None) -> list[str]:
    """--- I5: `auoreo web`/`serve` on a non-loopback bind prints where the LAN reaches it
    (a wildcard bind lists every non-loopback IPv4 of this host)."""
    if host in LOOPBACK_HOSTS or host.startswith("127."):
        return []
    wildcard = host in {"0.0.0.0", "::", ""}
    targets = (ips if ips is not None else lan_ipv4s()) if wildcard else [host]
    return [f"lan http://{f'[{ip}]' if ':' in ip else ip}:{port}" for ip in targets]


def _check_ws(st: Settings, ws_ping_interval: float | None, ws_ping_timeout: float | None) -> None:
    for flag, value in (("--ws-ping-interval", ws_ping_interval), ("--ws-ping-timeout", ws_ping_timeout)):
        if value is not None and value <= 0:
            raise ServeOptionError(f"{flag} must be > 0")
    if ws_ping_interval is not None:
        st.ws_ping_interval_s = ws_ping_interval
    if ws_ping_timeout is not None:
        st.cli.ws_ping_timeout_s = ws_ping_timeout


def _check_ws_auth(st: Settings, ws_auth: str | None) -> None:
    if ws_auth is not None:
        if ws_auth not in WS_AUTH_CHOICES:
            raise ServeOptionError(f"--ws-auth must be one of {', '.join(WS_AUTH_CHOICES)}")
        st.cli.ws_auth = ws_auth  # type: ignore[assignment]
    if st.cli.ws_auth == "off" and not st.is_loopback:
        raise ServeOptionError("--ws-auth off is only allowed on a loopback bind (127.0.0.1/::1)")
    if st.cli.ws_auth in TOKEN_WS_AUTH and not st.server_password:
        raise ServeOptionError(
            f"--ws-auth {st.cli.ws_auth} mints tokens from the server password; set AUOREO_SERVER_PASSWORD"
        )


def _check_tunnel(st: Settings, tunnel: str) -> None:
    if tunnel not in TUNNEL_CHOICES:
        raise ServeOptionError(f"--tunnel must be one of {', '.join(TUNNEL_CHOICES)}")
    if tunnel == "off":
        return
    if not st.server_password:
        raise ServeOptionError(f"--tunnel {tunnel} needs AUOREO_SERVER_PASSWORD (tunnels are fail-closed)")
    if st.cli.ws_auth == "off":
        raise ServeOptionError("--ws-auth off cannot be combined with a public --tunnel")


def resolve(
    st: Settings,
    *,
    ws: bool = True,
    ws_ping_interval: float | None = None,
    ws_ping_timeout: float | None = None,
    tunnel: str = "off",
    cors: list[str] | None = None,
    trusted_hosts: list[str] | None = None,
    ws_auth: str | None = None,
    mdns: bool = False,
) -> ServeExtras:
    """Apply the flags to `st` (this process only) and validate them. Raises ServeOptionError."""
    _check_ws(st, ws_ping_interval, ws_ping_timeout)
    if cors:
        st.cors_origins = [*st.cors_origins, *(o.rstrip("/") for o in cors)]
    if trusted_hosts:
        st.http.trusted_hosts = [*st.http.trusted_hosts, *trusted_hosts]
    if mdns:
        st.host = "0.0.0.0"  # advertised on the LAN, so it must be reachable there
    _check_ws_auth(st, ws_auth)
    _check_tunnel(st, tunnel)
    try:  # --- H1b: exposed binds force `forbid-file` (no plaintext key files)
        keyring_policy.enforce_public_bind(st, mdns=mdns)
    except keyring_policy.KeyringPolicyError as exc:
        raise ServeOptionError(str(exc)) from exc
    if mdns:
        try:
            import zeroconf  # noqa: F401
        except ImportError as exc:
            raise ServeOptionError(MDNS_INSTALL) from exc
    return ServeExtras(ws=ws, tunnel=tunnel, mdns=mdns)
