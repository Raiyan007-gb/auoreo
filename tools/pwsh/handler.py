"""PowerShell through the selected sandbox backend (spec §9.3, §19.8); output like `bash`."""

import shutil
import sys

from auoreo.backends.sandbox import ExecSpec, SandboxError, denial_text, sandbox_note
from auoreo.tools.context import ToolContext, ToolError, ToolResult

CAP = 30_000
CANDIDATES = ("pwsh", "pwsh.exe", "powershell.exe", "powershell") if sys.platform == "win32" else ("pwsh",)
MISSING = (
    "PowerShell is not installed: neither `pwsh` nor `powershell.exe` is on PATH. Install PowerShell 7 "
    "(https://aka.ms/powershell) or use the bash tool instead."
)


def _clip(text: str) -> str:
    if len(text) <= CAP:
        return text
    head, tail = text[: CAP // 2], text[-CAP // 4 :]
    return f"{head}\n… [{len(text) - len(head) - len(tail)} chars omitted] …\n{tail}"


def _find(path: str) -> str | None:
    for name in CANDIDATES:
        if found := shutil.which(name, path=path):
            return found
    return None


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    exe = _find(ctx.env.get("PATH", ""))
    if exe is None:
        raise ToolError(MISSING)
    timeout = int(args.get("timeout_s", 120))
    policy = ctx.exec_policy
    spec = ExecSpec(
        argv=[exe, "-NoProfile", "-NonInteractive", "-Command", args["script"]],
        cwd=ctx.cwd,
        env=dict(ctx.env),
        timeout_s=timeout,
        mode=policy.mode,  # type: ignore[arg-type]
        net=policy.net,  # type: ignore[arg-type]
        merge_stderr=True,
    )
    try:
        res = await ctx.backend("sandbox").run(spec)
    except SandboxError as exc:
        raise ToolError(f"[sandbox: {exc}]") from exc
    note = sandbox_note(ctx, res)
    if res.timed_out:
        text = f"script timed out after {timeout}s"
        return ToolResult(f"{text}\n{note}" if note else text, is_error=True)
    code = res.exit_code
    lines = [_clip(res.stdout + res.stderr), f"[exit code {code}]"]
    if res.ro_denied(spec):
        lines.append(denial_text(spec.mode))
    if note:
        lines.append(note)
    meta = {
        "exit_code": code,
        "sandbox": res.backend,
        "enforced": res.enforced,
        "mode": spec.mode,
        "shell": exe,
    }
    return ToolResult("\n".join(lines).lstrip(), is_error=code != 0, meta=meta)
