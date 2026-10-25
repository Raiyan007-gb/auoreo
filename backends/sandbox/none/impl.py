"""No-op sandbox: runs the argv as a plain subprocess and never claims isolation."""

from __future__ import annotations

from auoreo.backends import BackendStatus
from auoreo.backends.sandbox import ExecResult, ExecSpec, SandboxBackend, run_local


class Backend(SandboxBackend):
    name = "none"

    async def check(self) -> BackendStatus:
        return BackendStatus(True, "no isolation: processes run as the server user")

    def capabilities(self) -> dict[str, bool]:
        return {"fs_isolation": False, "net_isolation": False}

    async def run(self, spec: ExecSpec) -> ExecResult:
        return await run_local(spec, backend=self.name, enforced=False)
