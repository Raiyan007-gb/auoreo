"""No-op sandbox: runs the argv as a plain subprocess and never claims isolation —
unless `security.sandbox_landlock` is on and the kernel has Landlock, in which case the
command runs under the Landlock helper (filesystem confinement only; reported honestly)."""

from __future__ import annotations

from auoreo.backends import BackendStatus, landlock
from auoreo.backends.sandbox import ExecResult, ExecSpec, SandboxBackend, run_local


class Backend(SandboxBackend):
    name = "none"

    def _landlock(self) -> int:
        security = getattr(self.settings, "security", None)
        return landlock.abi() if getattr(security, "sandbox_landlock", False) else 0

    async def check(self) -> BackendStatus:
        security = getattr(self.settings, "security", None)
        if getattr(security, "sandbox_landlock", False):
            version = landlock.abi()
            if version > 0:
                return BackendStatus(True, f"no namespace isolation; {landlock.describe(version)} layered")
            return BackendStatus(
                True, f"no isolation: {landlock.describe(version)}; processes run as the server user"
            )
        return BackendStatus(True, "no isolation: processes run as the server user")

    def capabilities(self) -> dict[str, bool]:
        version = self._landlock()
        return {"fs_isolation": version > 0, "net_isolation": version >= 4}

    async def run(self, spec: ExecSpec) -> ExecResult:
        if self._landlock() <= 0:
            return await run_local(spec, backend=self.name, enforced=False)
        await self.check_mounts(spec)
        wrapped = ExecSpec(
            argv=landlock.wrap(
                spec.argv,
                cwd=spec.cwd,
                mode=spec.mode,
                net=spec.net,
                readable=spec.readable,
                writable=spec.writable,
            ),
            cwd=spec.cwd,
            env=spec.env,
            stdin=spec.stdin,
            timeout_s=spec.timeout_s,
            mode=spec.mode,
            net=spec.net,
            merge_stderr=spec.merge_stderr,
        )
        return await run_local(wrapped, backend=self.name, enforced=True)
