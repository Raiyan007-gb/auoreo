"""Unix-domain-socket plumbing for the daemon: bind (0600 socket, 0700 parent), the
"trust the socket" ASGI wrapper, and the probe the CLI uses to auto-connect.

The socket is local-user-only through file permissions, so a request that arrives over it
needs no bearer token: `TrustUnixSocket` vouches for it by presenting the configured server
password to the app's normal auth dependency. TCP requests are untouched.
"""

from __future__ import annotations

import contextlib
import os
import socket
import stat
from pathlib import Path
from typing import Any

import httpx
from starlette.types import ASGIApp, Receive, Scope, Send

SOCKET_NAME = "auoreo.sock"
UDS_BASE_URL = "http://auoreo"
"""Host part is ignored on a UDS transport; any valid authority works."""
NO_DAEMON_ENV = "AUOREO_NO_DAEMON"


def supported() -> bool:
    return os.name == "posix" and hasattr(socket, "AF_UNIX")


def socket_path(home: Path) -> Path:
    return home / SOCKET_NAME


def bind_unix(path: Path) -> socket.socket:
    """Bind a listening AF_UNIX socket at `path`: parent 0700, socket 0600, stale file removed.

    A path that exists but is not a socket is refused (never unlink a regular file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    if path.exists() or path.is_symlink():
        if not stat.S_ISSOCK(path.lstat().st_mode):
            raise OSError(f"{path} exists and is not a socket")
        if probe(path):
            raise OSError(f"{path} is in use by a running server")
        path.unlink()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)  # the socket file is born 0600: no window with wider perms
    try:
        sock.bind(str(path))
    finally:
        os.umask(old)
    os.chmod(path, 0o600)
    sock.listen(128)
    sock.setblocking(False)
    return sock


def is_unix_scope(scope: Scope) -> bool:
    """uvicorn reports a UDS listener as `server=(path, None)`; TCP always has a port."""
    server = scope.get("server")
    return server is not None and len(server) == 2 and server[1] is None


class TrustUnixSocket:
    """ASGI wrapper: requests over the Unix socket are authenticated by file permissions."""

    def __init__(self, app: ASGIApp, password: str | None) -> None:
        self.app = app
        self.password = password

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.password and scope["type"] in {"http", "websocket"} and is_unix_scope(scope):
            headers = [(k, v) for k, v in scope.get("headers", []) if k != b"authorization"]
            headers.append((b"authorization", f"Bearer {self.password}".encode()))
            scope = {**scope, "headers": headers}
        await self.app(scope, receive, send)


def probe(path: Path, timeout: float = 1.0) -> dict[str, Any] | None:
    """`/api/health` over the socket, or None when nothing (healthy) answers."""
    if not supported() or not path.exists():
        return None
    try:
        with httpx.Client(transport=httpx.HTTPTransport(uds=str(path)), timeout=timeout) as c:
            r = c.get(f"{UDS_BASE_URL}/api/health")
    except (httpx.HTTPError, OSError):
        return None
    if r.status_code != 200:
        return None
    with contextlib.suppress(ValueError):
        body = r.json()
        return body if isinstance(body, dict) else None
    return None


def daemon_socket(home: Path) -> Path | None:
    """The one transport decision for the CLI: a live daemon socket at `<home>/auoreo.sock`
    (unless `AUOREO_NO_DAEMON=1`) → use it; None → run in-process."""
    if os.environ.get(NO_DAEMON_ENV, "").lower() in {"1", "true", "yes"}:
        return None
    path = socket_path(home)
    return path if probe(path) is not None else None


def client(path: Path, timeout: float | None = 30) -> httpx.Client:
    return httpx.Client(transport=httpx.HTTPTransport(uds=str(path)), base_url=UDS_BASE_URL, timeout=timeout)
