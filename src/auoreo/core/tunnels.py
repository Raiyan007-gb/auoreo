"""Public tunnels to the local server: cloudflared quick tunnels, tailscale serve, fake.

Exposing the server publicly is fail-closed: `start` refuses without
`AUOREO_SERVER_PASSWORD`. State lives in `<home>/tunnel.json {provider, pid, url, port}`.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

CLOUDFLARED_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
HTTPS_URL_RE = re.compile(r"https://[^\s]+")
FAKE_URL = "https://fake-tunnel.trycloudflare.com"


class TunnelError(RuntimeError):
    """Refusal or failure with a human-readable fix hint."""


@dataclass
class TunnelInfo:
    provider: str
    pid: int | None
    url: str
    port: int

    @property
    def ws_url(self) -> str:
        return ws_url(self.url)


def ws_url(url: str) -> str:
    base = url.rstrip("/")
    return re.sub(r"^http", "ws", base, count=1) + "/ws"


class TunnelProvider(Protocol):
    name: str
    binary: str | None
    install_url: str

    def start(self, port: int, log_path: Path, timeout_s: float) -> TunnelInfo: ...

    def stop(self, info: TunnelInfo) -> None: ...


def _kill(pid: int | None) -> None:
    if not pid:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGTERM)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(pid, signal.SIGTERM)


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Cloudflared:
    """`cloudflared tunnel --url http://127.0.0.1:<port>`; URL parsed from its stderr log."""

    name = "cloudflared"
    binary: str | None = "cloudflared"
    install_url = "https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"

    def start(self, port: int, log_path: Path, timeout_s: float) -> TunnelInfo:
        log = log_path.open("wb")
        proc = subprocess.Popen(
            [self.binary or "cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,  # survives the CLI; stopped via its process group
        )
        log.close()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            m = CLOUDFLARED_URL_RE.search(log_path.read_text(encoding="utf-8", errors="replace"))
            if m:
                return TunnelInfo(self.name, proc.pid, m.group(0), port)
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        _kill(proc.pid)
        raise TunnelError(f"cloudflared did not report a public URL; see {log_path}")

    def stop(self, info: TunnelInfo) -> None:
        _kill(info.pid)


class Tailscale:
    """`tailscale serve --bg <port>`, then `tailscale serve status` for the URL."""

    name = "tailscale"
    binary: str | None = "tailscale"
    install_url = "https://tailscale.com/download"

    def start(self, port: int, log_path: Path, timeout_s: float) -> TunnelInfo:
        exe = self.binary or "tailscale"
        run = subprocess.run(
            [exe, "serve", "--bg", str(port)], capture_output=True, text=True, timeout=timeout_s
        )
        log_path.write_text(run.stdout + run.stderr, encoding="utf-8")
        if run.returncode != 0:
            raise TunnelError(f"tailscale serve failed: {(run.stderr or run.stdout).strip()[:300]}")
        status = subprocess.run([exe, "serve", "status"], capture_output=True, text=True, timeout=timeout_s)
        m = HTTPS_URL_RE.search(status.stdout) or HTTPS_URL_RE.search(run.stdout)
        if m is None:
            raise TunnelError("tailscale serve is running but reported no https URL")
        return TunnelInfo(self.name, None, m.group(0).rstrip("/"), port)

    def stop(self, info: TunnelInfo) -> None:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run([self.binary or "tailscale", "serve", "reset"], capture_output=True, timeout=30)


class FakeTunnel:
    """Test/demo provider: no process, a canned URL."""

    name = "fake"
    binary: str | None = None
    install_url = ""

    def start(self, port: int, log_path: Path, timeout_s: float) -> TunnelInfo:
        log_path.write_text(f"fake tunnel → {FAKE_URL}\n", encoding="utf-8")
        return TunnelInfo(self.name, None, FAKE_URL, port)

    def stop(self, info: TunnelInfo) -> None:
        return None


PROVIDERS: dict[str, type[Cloudflared] | type[Tailscale] | type[FakeTunnel]] = {
    "cloudflared": Cloudflared,
    "tailscale": Tailscale,
    "fake": FakeTunnel,
}


class TunnelManager:
    def __init__(self, home: Path, server_password: str | None) -> None:
        self.home = home
        self.server_password = server_password
        self.state_path = home / "tunnel.json"
        self.log_path = home / "tunnel.log"

    def provider(self, name: str) -> TunnelProvider:
        try:
            return PROVIDERS[name]()
        except KeyError:
            raise TunnelError(f"unknown tunnel provider '{name}' (known: {sorted(PROVIDERS)})") from None

    def status(self) -> TunnelInfo | None:
        if not self.state_path.is_file():
            return None
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return TunnelInfo(**data)
        except (OSError, ValueError, TypeError):
            return None

    def start(self, provider_name: str, port: int, timeout_s: float = 30.0) -> TunnelInfo:
        if not self.server_password:
            raise TunnelError(
                "refusing to expose the server publicly without a password: "
                "set AUOREO_SERVER_PASSWORD (e.g. `export AUOREO_SERVER_PASSWORD=$(openssl rand -hex 24)`) "
                "and restart `auoreo serve`"
            )
        current = self.status()
        if current is not None and (current.pid is None or pid_alive(current.pid)):
            raise TunnelError(
                f"a {current.provider} tunnel is already running at {current.url}; stop it first"
            )
        provider = self.provider(provider_name)
        if provider.binary is not None and shutil.which(provider.binary) is None:
            raise TunnelError(
                f"'{provider.binary}' not found on PATH; install it from {provider.install_url}"
            )
        self.home.mkdir(parents=True, exist_ok=True)
        info = provider.start(port, self.log_path, timeout_s)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(info), indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.state_path)
        return info

    def stop(self) -> TunnelInfo | None:
        info = self.status()
        if info is None:
            return None
        with contextlib.suppress(TunnelError):
            self.provider(info.provider).stop(info)
        self.state_path.unlink(missing_ok=True)
        return info
