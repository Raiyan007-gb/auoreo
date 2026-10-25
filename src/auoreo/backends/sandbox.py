"""Sandbox backend kind (`backends/sandbox/<name>/`, spec §19.8).

A sandbox runs one argv under a confinement *mode* and *net* policy and reports honestly
whether that confinement was enforced. Tools never spawn processes for model-supplied
commands themselves: they build an `ExecSpec` and call `ctx.backend("sandbox").run(spec)`.

Modes order `read-only < workspace-write < danger-full-access`; a host mount outside
`cwd` needs `danger-full-access` (and is audited as `sandbox.mount`).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from auoreo.backends import BackendStatus

if TYPE_CHECKING:
    from auoreo.core.settings import Settings
    from auoreo.tools.context import ToolContext

Mode = Literal["read-only", "workspace-write", "danger-full-access"]
Net = Literal["none", "proxy", "direct"]
MODES: tuple[Mode, ...] = ("read-only", "workspace-write", "danger-full-access")
AuditFn = Callable[[str, dict[str, Any]], Awaitable[None]]

NOT_ENFORCED_NOTE = "[sandbox: not enforced — backend '{name}']"
_RO_DENIED = re.compile(r"read-only file system|EROFS", re.I)


def mode_rank(mode: str) -> int:
    return MODES.index(mode)


def denial_text(mode: str) -> str:
    wider = MODES[min(mode_rank(mode) + 1, len(MODES) - 1)]
    return (
        f"[sandbox: file access denied under {mode} mode] — retry with "
        f'sandbox_mode="{wider}" and a justification'
    )


@dataclass(frozen=True)
class ExecSpec:
    argv: list[str]
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)
    stdin: bytes | None = None
    timeout_s: float = 120.0
    mode: Mode = "workspace-write"
    net: Net = "none"
    writable: list[Path] = field(default_factory=list)
    readable: list[Path] = field(default_factory=list)
    merge_stderr: bool = False
    """Send stderr into stdout (interleaved), like `2>&1`; `stderr` is then empty."""

    def outside_cwd(self) -> list[Path]:
        """Extra mounts that are not inside `cwd` (they need `danger-full-access`)."""
        root = self.cwd.resolve()
        out: list[Path] = []
        for p in [*self.readable, *self.writable]:
            rp = p.resolve()
            if rp != root and root not in rp.parents:
                out.append(rp)
        return out


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    backend: str
    enforced: bool

    def ro_denied(self, spec: ExecSpec) -> bool:
        """True when an enforced read-only sandbox blocked a write."""
        if not self.enforced or spec.mode != "read-only" or self.exit_code == 0:
            return False
        return bool(_RO_DENIED.search(self.stdout) or _RO_DENIED.search(self.stderr))


class SandboxError(Exception):
    """The spec cannot run under this backend (e.g. a host mount without full access)."""


class SandboxBackend(ABC):
    name = "sandbox"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.audit: AuditFn | None = None
        """Set by the sandbox starter so backends can audit mounts (`sandbox.mount`)."""

    @abstractmethod
    async def check(self) -> BackendStatus:
        """Whether this backend can confine here (never raises)."""

    @abstractmethod
    def capabilities(self) -> dict[str, bool]:
        """`{fs_isolation, net_isolation}` this backend enforces when `check()` passes."""

    @abstractmethod
    async def run(self, spec: ExecSpec) -> ExecResult:
        """Run `spec.argv`; kill it after `timeout_s` (then `timed_out=True`, exit -9)."""

    async def _audit(self, action: str, detail: dict[str, Any]) -> None:
        if self.audit is not None:
            await self.audit(action, detail)

    async def check_mounts(self, spec: ExecSpec) -> list[Path]:
        """Validate host mounts beyond `cwd`; audit them. Raises `SandboxError`."""
        extra = spec.outside_cwd()
        if not extra:
            return []
        if spec.mode != "danger-full-access":
            raise SandboxError(
                f"host mounts outside the working directory ({', '.join(map(str, extra))}) "
                'need sandbox_mode="danger-full-access" and an approved justification'
            )
        await self._audit("sandbox.mount", {"backend": self.name, "args": [str(p) for p in extra]})
        return extra


async def run_local(spec: ExecSpec, *, backend: str, enforced: bool) -> ExecResult:
    """Plain subprocess in its own session; the whole process group dies on timeout."""
    proc = await asyncio.create_subprocess_exec(
        *spec.argv,
        cwd=str(spec.cwd),
        env=spec.env or None,
        stdin=asyncio.subprocess.PIPE if spec.stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT if spec.merge_stderr else asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(spec.stdin), timeout=spec.timeout_s)
    except (TimeoutError, asyncio.CancelledError):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise
        return ExecResult(-9, "", "", True, backend, enforced)
    return ExecResult(
        proc.returncode if proc.returncode is not None else -1,
        (out or b"").decode("utf-8", errors="replace"),
        (err or b"").decode("utf-8", errors="replace"),
        False,
        backend,
        enforced,
    )


def sandbox_note(ctx: ToolContext, result: ExecResult) -> str:
    """One line telling the model isolation was NOT enforced — only the first time per
    session (never silently claim isolation, never nag)."""
    if result.enforced or ctx.state.sandbox_note_shown:
        return ""
    ctx.state.sandbox_note_shown = True
    return NOT_ENFORCED_NOTE.format(name=result.backend)


# ------------------------------------------------------------------ escalation (§24.9)
_GENERIC = frozenset(
    {
        "need access",
        "needs access",
        "i need access",
        "access needed",
        "need permission",
        "permission needed",
        "required",
        "necessary",
        "please",
        "please allow",
        "to complete the task",
        "for the task",
    }
)
_PATH_RE = re.compile(r"(^|\s)(~|\.{0,2}/)\S*|\b[\w.-]+\.[A-Za-z0-9]{1,8}\b|\$HOME|\bdir(ectory)?\b")
_OP_RE = re.compile(
    r"\b(writ|creat|delet|remov|install|build|compil|mkdir|touch|cop(y|ies)|mov|renam|chmod|chown|"
    r"edit|modif|updat|download|cach|generat|format|mount|sav|output|log|test|clean|extract|unzip|"
    r"untar|link|append|truncat|commit|push|pull|clone|fetch)\w*",
    re.I,
)


def justification_error(text: str) -> str | None:
    """Why a justification is unacceptable, or None. It must be 10–500 chars and name
    the denied path or operation; generic pleas are refused without a prompt."""
    j = " ".join(text.split())
    if not 10 <= len(j) <= 500:
        return "justification must be 10–500 characters"
    if j.lower().strip(" .!") in _GENERIC:
        return "justification is generic; name the path or operation that needs the wider mode"
    if not (_PATH_RE.search(j) or _OP_RE.search(j)):
        return "justification must mention the path or operation that needs the wider mode"
    return None


def escalation_request(manifest_mode: str, args: dict[str, Any]) -> tuple[str, str | None, str | None]:
    """`(effective_mode, wider_mode_or_None, error_or_None)` from a call's optional
    `sandbox_mode` + `justification` arguments (a narrower mode needs no grant)."""
    requested = args.get("sandbox_mode")
    justification = args.get("justification")
    if requested is None:
        if justification is not None:
            return manifest_mode, None, "justification given without a sandbox_mode; drop it"
        return manifest_mode, None, None
    if requested not in MODES:
        return manifest_mode, None, f"sandbox_mode must be one of {', '.join(MODES)}"
    if mode_rank(requested) <= mode_rank(manifest_mode):
        if justification is not None:
            return (
                manifest_mode,
                None,
                f"justification given without a mode bump ({requested} is not wider than "
                f"{manifest_mode}); drop the justification",
            )
        return str(requested), None, None
    why = justification_error(justification if isinstance(justification, str) else "")
    if why is not None:
        return manifest_mode, None, f'sandbox_mode="{requested}" refused: {why}'
    return manifest_mode, str(requested), None
