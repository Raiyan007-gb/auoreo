"""macOS Seatbelt sandbox (spec §19.8, §25.10): `/usr/bin/sandbox-exec -p <profile> argv…`.

The SBPL profile is generated per execution (pure `build_profile`, unit-tested anywhere):
`(deny default)`, process exec/fork, read-only system roots, read-write cwd + temp dirs
(read-only under `read-only` mode), and network per `net` (none → nothing, proxy →
loopback only, direct → outbound). `danger-full-access` is never run under seatbelt. Only the
absolute `/usr/bin/sandbox-exec` is used, and `check()` is honest off macOS.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

from auoreo.backends import BackendStatus
from auoreo.backends.sandbox import ExecResult, ExecSpec, SandboxBackend, run_local

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
REMEDY = 'seatbelt needs macOS; on Linux select settings.backends.sandbox = "bwrap" | "docker"'
SYSTEM_READ: tuple[str, ...] = (
    "/usr",
    "/bin",
    "/sbin",
    "/System",
    "/Library",
    "/Applications/Xcode.app",
    "/opt/homebrew",
    "/usr/local",
    "/private/etc",
    "/private/var/db/timezone",
    "/dev",
)
TEMP_DIRS: tuple[str, ...] = ("/private/tmp", "/private/var/folders")
BASE = """(version 1)
(deny default)
(allow process-exec)
(allow process-fork)
(allow signal (target same-sandbox))
(allow sysctl-read)
(allow mach-lookup)
(allow ipc-posix-shm)
(allow file-read-metadata)
(allow file-read* (literal "/"))
(allow file-write* (literal "/dev/null") (literal "/dev/tty") (literal "/dev/dtracehelper"))"""


def _q(path: str | Path) -> str:
    """An SBPL string literal (backslash + quote escaped)."""
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _subpaths(paths: list[str]) -> str:
    return " ".join(f"(subpath {_q(p)})" for p in paths)


def build_profile(spec: ExecSpec, *, tmpdir: str | None = None) -> str:
    """The SBPL profile for `spec` (pure)."""
    if spec.mode == "danger-full-access":
        raise ValueError("danger-full-access never runs under seatbelt (use docker/ssh)")
    tmp = [*TEMP_DIRS, os.path.realpath(tmpdir or tempfile.gettempdir())]
    read = [*SYSTEM_READ, sys.base_prefix, sys.prefix, *map(str, spec.readable)]
    lines = [BASE, f"(allow file-read* {_subpaths(sorted(set(read)))})"]
    cwd = str(spec.cwd)
    if spec.mode == "read-only":
        lines.append(f"(allow file-read* (subpath {_q(cwd)}))")
        lines.append(f"(allow file-read* file-write* {_subpaths(sorted(set(tmp)))})")
    else:
        rw = sorted({cwd, *tmp, *map(str, spec.writable)})
        lines.append(f"(allow file-read* file-write* {_subpaths(rw)})")
    net = {
        "none": "",
        "proxy": '(allow network-outbound (remote ip "localhost:*"))',
        "direct": "(allow network-outbound)\n(allow system-socket)",
    }[spec.net]
    if net:
        lines.append(net)
    return "\n".join(lines) + "\n"


def profile_hash(profile: str) -> str:
    return hashlib.sha256(profile.encode()).hexdigest()[:16]


def build_argv(spec: ExecSpec, *, tmpdir: str | None = None) -> list[str]:
    return [SANDBOX_EXEC, "-p", build_profile(spec, tmpdir=tmpdir), *spec.argv]


class Backend(SandboxBackend):
    name = "seatbelt"

    async def check(self) -> BackendStatus:
        if sys.platform != "darwin":
            return BackendStatus(False, f"seatbelt is macOS-only (this is {sys.platform})", remedy=REMEDY)
        if not os.access(SANDBOX_EXEC, os.X_OK):
            return BackendStatus(False, f"{SANDBOX_EXEC} not found", remedy=REMEDY)
        return BackendStatus(True, "sandbox-exec available (deny-default SBPL profile per call)")

    def capabilities(self) -> dict[str, bool]:
        return {"fs_isolation": True, "net_isolation": True}

    async def run(self, spec: ExecSpec) -> ExecResult:
        await self.check_mounts(spec)
        status = await self.check()
        if not status.ok:
            return ExecResult(127, "", f"{status.detail}; {REMEDY}", False, self.name, False)
        wrapped = ExecSpec(
            argv=build_argv(spec),
            cwd=Path(spec.cwd),
            env=spec.env,
            stdin=spec.stdin,
            timeout_s=spec.timeout_s,
            mode=spec.mode,
            net=spec.net,
            merge_stderr=spec.merge_stderr,
        )
        return await run_local(wrapped, backend=self.name, enforced=True)
