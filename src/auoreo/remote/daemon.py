"""`auoreo daemon start|stop|status`: the same app (`create_app`) detached on
`<home>/auoreo.sock`, so the CLI talks to one long-lived process instead of booting
`Services` per command.

`start` re-executes `auoreo serve --uds <sock>` in a new session (`start_new_session=True`,
the portable equivalent of a double fork: no controlling terminal, survives the shell),
writes `<home>/daemon.pid` and appends to `<home>/logs/daemon.log`. `stop` sends SIGTERM
and waits up to 10 s. POSIX only.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from auoreo.remote import uds

PID_NAME = "daemon.pid"
LOG_NAME = "daemon.log"
STOP_TIMEOUT_S = 10.0


class DaemonError(RuntimeError):
    pass


@dataclass
class DaemonPaths:
    home: Path

    @property
    def sock(self) -> Path:
        return uds.socket_path(self.home)

    @property
    def pid(self) -> Path:
        return self.home / PID_NAME

    @property
    def log(self) -> Path:
        return self.home / "logs" / LOG_NAME


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_pid(paths: DaemonPaths) -> int | None:
    try:
        pid = int(paths.pid.read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if _alive(pid) else None


def start(
    paths: DaemonPaths,
    *,
    port: int | None = None,
    gateway: str | None = None,
    wait_s: float = 30.0,
) -> tuple[int, dict[str, Any]]:
    """Spawn the daemon and wait until it answers `/api/health` over the socket."""
    if not uds.supported():
        raise DaemonError("the daemon needs a POSIX system (Unix domain sockets)")
    if (pid := read_pid(paths)) is not None and (health := uds.probe(paths.sock)) is not None:
        raise DaemonError(f"already running (pid {pid}, {health.get('version')})")
    paths.home.mkdir(parents=True, exist_ok=True)
    paths.log.parent.mkdir(parents=True, exist_ok=True)
    argv = [sys.executable, "-m", "auoreo.cli.main", "serve", "--uds", str(paths.sock)]
    if port is not None:
        argv += ["--port", str(port)]
    if gateway:
        argv += ["--gateway", gateway]
    env = {**os.environ, "AUOREO_HOME": str(paths.home), uds.NO_DAEMON_ENV: "1"}
    with open(paths.log, "ab") as log:
        proc = subprocess.Popen(  # noqa: S603 - our own interpreter + fixed argv
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
            close_fds=True,
        )
    paths.pid.write_text(f"{proc.pid}\n")
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            paths.pid.unlink(missing_ok=True)
            raise DaemonError(f"daemon exited with {proc.returncode}; see {paths.log}")
        if (health := uds.probe(paths.sock, timeout=0.5)) is not None:
            return proc.pid, health
        time.sleep(0.1)
    raise DaemonError(f"daemon did not answer on {paths.sock} within {wait_s:.0f}s; see {paths.log}")


def stop(paths: DaemonPaths, timeout_s: float = STOP_TIMEOUT_S) -> int | None:
    """SIGTERM the pid in the pid file and wait for it; returns the pid (None: not running)."""
    pid = read_pid(paths)
    if pid is None:
        paths.pid.unlink(missing_ok=True)
        return None
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and _alive(pid):
        _reap(pid)
        time.sleep(0.1)
    if _alive(pid):
        raise DaemonError(f"pid {pid} did not exit within {timeout_s:.0f}s")
    paths.pid.unlink(missing_ok=True)
    if paths.sock.exists() and uds.probe(paths.sock) is None:
        paths.sock.unlink(missing_ok=True)
    return pid


def _reap(pid: int) -> None:
    """Collect the child if this process happens to be its parent (tests); else a no-op."""
    with contextlib.suppress(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)


def status(paths: DaemonPaths) -> dict[str, Any]:
    health = uds.probe(paths.sock)
    return {
        "running": health is not None,
        "pid": read_pid(paths),
        "socket": str(paths.sock),
        "log": str(paths.log),
        "health": health,
    }
