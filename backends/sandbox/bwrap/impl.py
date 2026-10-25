"""bubblewrap sandbox (spec §26.15) with a Landlock layer inside the namespace (§19.8).

When the kernel reports a Landlock ABI and a system `python3` is reachable inside bwrap,
the command runs under `auoreo.backends.landlock`'s helper (read-only system roots, writable
cwd + /tmp, TCP denied for `net: none` on ABI >= 4). `check()` reports the ABI honestly."""

from __future__ import annotations

import shutil
from pathlib import Path

from auoreo.backends import BackendStatus, landlock
from auoreo.backends.sandbox import ExecResult, ExecSpec, SandboxBackend, run_local

REMEDY = (
    "install bubblewrap (apt install bubblewrap / dnf install bubblewrap) or select another "
    'backend with settings.backends.sandbox = "docker" | "none"'
)
SYSTEM_RO = ("/bin", "/sbin", "/lib", "/lib64", "/lib32")
ETC_RO = (
    "/etc/alternatives",
    "/etc/ld.so.cache",
    "/etc/ld.so.conf",
    "/etc/ld.so.conf.d",
    "/etc/passwd",
    "/etc/group",
    "/etc/nsswitch.conf",
    "/etc/resolv.conf",
    "/etc/hosts",
    "/etc/ssl",
    "/etc/ca-certificates",
    "/etc/localtime",
)


def build_argv(spec: ExecSpec, bwrap: str = "bwrap", landlock_python: str | None = None) -> list[str]:
    """The full bwrap command line for `spec` (pure; unit-tested). With `landlock_python`
    the inner argv is wrapped in the Landlock helper (applied after bwrap's mounts)."""
    cwd = str(spec.cwd)
    argv = [bwrap, "--ro-bind", "/usr", "/usr"]
    for p in SYSTEM_RO:
        argv += ["--ro-bind-try", p, p]
    for p in ETC_RO:
        argv += ["--ro-bind-try", p, p]
    argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    argv += ["--unshare-pid", "--unshare-user-try", "--unshare-ipc", "--unshare-uts"]
    if spec.net == "none":
        argv.append("--unshare-net")
    argv += ["--die-with-parent", "--new-session", "--cap-drop", "ALL"]
    argv += ["--ro-bind" if spec.mode == "read-only" else "--bind", cwd, cwd]
    for p in spec.readable:
        argv += ["--ro-bind", str(p), str(p)]
    if spec.mode != "read-only":
        for p in spec.writable:
            argv += ["--bind", str(p), str(p)]
    argv += ["--chdir", cwd, "--"]
    inner = list(spec.argv)
    if landlock_python is not None:
        inner = landlock.wrap(
            inner,
            cwd=spec.cwd,
            mode=spec.mode,
            net=spec.net,
            readable=spec.readable,
            writable=spec.writable,
            python=landlock_python,
            tmp="/tmp",
        )
    return argv + inner


def landlock_python() -> str | None:
    """The interpreter for the Landlock helper, or None when Landlock cannot be layered."""
    return landlock.system_python() if landlock.abi() > 0 else None


class Backend(SandboxBackend):
    name = "bwrap"

    async def check(self) -> BackendStatus:
        if shutil.which("bwrap") is None:
            return BackendStatus(False, "bwrap not installed", remedy=REMEDY)
        version = landlock.abi()
        if version > 0 and landlock.system_python() is None:
            return BackendStatus(
                True, f"bubblewrap available; {landlock.describe(version)} not layered (no /usr/bin/python3)"
            )
        layered = "layered" if version > 0 else "not layered"
        return BackendStatus(True, f"bubblewrap available; {landlock.describe(version)} {layered}")

    def capabilities(self) -> dict[str, bool]:
        return {"fs_isolation": True, "net_isolation": True}

    async def run(self, spec: ExecSpec) -> ExecResult:
        await self.check_mounts(spec)
        bwrap = shutil.which("bwrap")
        if bwrap is None:
            return ExecResult(127, "", f"bwrap not installed; {REMEDY}", False, self.name, False)
        wrapped = ExecSpec(
            argv=build_argv(spec, bwrap, landlock_python()),
            cwd=Path(spec.cwd),
            env=spec.env,
            stdin=spec.stdin,
            timeout_s=spec.timeout_s,
            mode=spec.mode,
            net=spec.net,
            merge_stderr=spec.merge_stderr,
        )
        return await run_local(wrapped, backend=self.name, enforced=True)
