"""bash through the selected sandbox backend (`ctx.backend("sandbox")`, spec §19.8)."""

from auoreo.backends.sandbox import ExecSpec, SandboxError, denial_text, sandbox_note
from auoreo.runtime.cwd import git_allow
from auoreo.tools.context import ToolContext, ToolError, ToolResult

CAP = 30_000


def _clip(text: str) -> str:
    if len(text) <= CAP:
        return text
    head, tail = text[: CAP // 2], text[-CAP // 4 :]
    return f"{head}\n… [{len(text) - len(head) - len(tail)} chars omitted] …\n{tail}"


async def run(args: dict, ctx: ToolContext) -> ToolResult:
    timeout = int(args.get("timeout_s", 120))
    policy = ctx.exec_policy
    allow = await git_allow(ctx)  # M7 W4: a worktree session may write its own git metadata
    spec = ExecSpec(
        argv=["bash", "-c", args["command"]],
        cwd=ctx.cwd,
        env=dict(ctx.env),
        timeout_s=timeout,
        mode=policy.mode,  # type: ignore[arg-type]
        net=policy.net,  # type: ignore[arg-type]
        merge_stderr=True,
        writable=allow,
        git_allow=allow,
    )
    sandbox = ctx.backend("sandbox")
    try:
        res = await sandbox.run(spec)
    except SandboxError as exc:
        raise ToolError(f"[sandbox: {exc}]") from exc
    note = sandbox_note(ctx, res)
    if res.timed_out:
        text = f"command timed out after {timeout}s"
        return ToolResult(f"{text}\n{note}" if note else text, is_error=True)
    code = res.exit_code
    lines = [_clip(res.stdout + res.stderr), f"[exit code {code}]"]
    if res.ro_denied(spec):
        lines.append(denial_text(spec.mode))
    if note:
        lines.append(note)
    meta = {"exit_code": code, "sandbox": res.backend, "enforced": res.enforced, "mode": spec.mode}
    if policy.grant_id:
        meta["grant_id"] = policy.grant_id
    return ToolResult("\n".join(lines).lstrip(), is_error=code != 0, meta=meta)
