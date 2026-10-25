"""bubblewrap sandbox (spec §26.15). Landlock is not layered here yet: the probe says so
honestly instead of claiming it (a helper binary is planned, see roadmap M5i)."""

from __future__ import annotations

import shutil
from pathlib import Path

from auoreo.backends import BackendStatus
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


def build_argv(spec: ExecSpec, bwrap: str = "bwrap") -> list[str]:
    """The full bwrap command line for `spec` (pure; unit-tested)."""
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
    return argv + list(spec.argv)


class Backend(SandboxBackend):
    name = "bwrap"

    async def check(self) -> BackendStatus:
        if shutil.which("bwrap") is None:
            return BackendStatus(False, "bwrap not installed", remedy=REMEDY)
        return BackendStatus(True, "bubblewrap available (landlock: unavailable, not layered)")

    def capabilities(self) -> dict[str, bool]:
        return {"fs_isolation": True, "net_isolation": True}

    async def run(self, spec: ExecSpec) -> ExecResult:
        await self.check_mounts(spec)
        bwrap = shutil.which("bwrap")
        if bwrap is None:
            return ExecResult(127, "", f"bwrap not installed; {REMEDY}", False, self.name, False)
        wrapped = ExecSpec(
            argv=build_argv(spec, bwrap),
            cwd=Path(spec.cwd),
            env=spec.env,
            stdin=spec.stdin,
            timeout_s=spec.timeout_s,
            mode=spec.mode,
            net=spec.net,
            merge_stderr=spec.merge_stderr,
        )
        return await run_local(wrapped, backend=self.name, enforced=True)
