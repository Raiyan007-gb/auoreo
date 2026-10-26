"""run_code (spec §9.3 P1): a snippet through the sandbox backend (`ctx.backend("sandbox")`),
fed on stdin so it never lands in argv, with a timeout and an output cap."""

import shutil

from auoreo.backends.sandbox import ExecSpec, SandboxError, denial_text, sandbox_note
from auoreo.core.settings_h5 import h5_of
from auoreo.tools.context import ToolContext, ToolError, ToolResult

INTERPRETERS: dict[str, tuple[list[str], list[str], str]] = {  # language → (candidates, args, hint)
    "python": (["python3", "python"], ["-I", "-"], "install Python 3 on PATH"),
    "javascript": (["node", "nodejs"], ["-"], "install Node.js (node on PATH)"),
    "bash": (["bash"], ["-s"], "install bash"),
}


def _clip(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    head, tail = text[: cap * 2 // 3], text[-cap // 4 :]
    return f"{head}\n… [{len(text) - len(head) - len(tail)} chars omitted] …\n{tail}"


def interpreter(language: str) -> list[str]:
    names, extra, hint = INTERPRETERS[language]
    exe = next((p for p in map(shutil.which, names) if p), None)
    if exe is None:
        raise ToolError(f"run_code: no {language} interpreter found ({hint})")
    return [exe, *extra]


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    cfg = h5_of(ctx).run_code
    language = args["language"]
    timeout = int(args.get("timeout_s") or cfg.timeout_s)
    policy = ctx.exec_policy
    spec = ExecSpec(
        argv=interpreter(language),
        cwd=ctx.cwd,
        env=dict(ctx.env),
        stdin=args["code"].encode("utf-8"),
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
        text = f"{language} snippet timed out after {timeout}s"
        return ToolResult(f"{text}\n{note}" if note else text, is_error=True, meta={"timed_out": True})
    lines = [_clip(res.stdout + res.stderr, cfg.max_output_chars), f"[exit code {res.exit_code}]"]
    if res.ro_denied(spec):
        lines.append(denial_text(spec.mode))
    if note:
        lines.append(note)
    meta = {"language": language, "exit_code": res.exit_code, "sandbox": res.backend, "mode": spec.mode}
    return ToolResult("\n".join(lines).lstrip(), is_error=res.exit_code != 0, meta=meta)
