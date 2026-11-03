"""Fake sandbox (tests and CI): nothing runs. Every `ExecSpec` is appended to `calls`, the
result comes from the `responses` script when one is queued (consumed in order) and otherwise
is exit 0 with the command text on stdout, and every result claims `enforced=True`, so code
paths that depend on an enforced sandbox can be exercised without bwrap, seatbelt or Docker.
It is never auto-probed (`sandbox_boot.AUTO_ORDER`): select it explicitly."""

from __future__ import annotations

from collections import deque
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.sandbox import ExecResult, ExecSpec, SandboxBackend

SHELL_FLAGS = frozenset({"-c", "-lc"})


def command_text(argv: list[str]) -> str:
    """`bash -c '<cmd>'` → `<cmd>`; anything else → the argv joined by spaces."""
    if len(argv) >= 3 and argv[1] in SHELL_FLAGS:
        return argv[2]
    return " ".join(argv)


class Backend(SandboxBackend):
    name = "fake"
    is_test_backend = True

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self.calls: list[ExecSpec] = []
        self.responses: deque[ExecResult] = deque()

    async def check(self) -> BackendStatus:
        return BackendStatus(True, "fake: nothing runs; every call is recorded and reported as enforced")

    def capabilities(self) -> dict[str, bool]:
        return {"fs_isolation": True, "net_isolation": True}

    def wrap_long_lived(self, spec: ExecSpec, *, tty: bool = False) -> tuple[list[str], bool] | None:
        """A long-lived process would really run on the host, so the fake refuses to wrap one."""
        return None

    async def run(self, spec: ExecSpec) -> ExecResult:
        self.calls.append(spec)
        if self.responses:
            return self.responses.popleft()
        return ExecResult(0, command_text(spec.argv), "", False, self.name, True)
