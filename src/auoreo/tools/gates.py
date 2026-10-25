"""Security gates the executor calls (spec §19.6, §19.9, §25.11), kept out of
`executor.py` so the pipeline file stays small:

- `external_dir_gate`: file tools whose `path` is outside cwd ask `external_directory`.
- `unattended_gate`: an `ask` with no human → `approvals.unattended.<kind>` (default deny).
- `secret_names`: the per-call secret scope (`requires_env` + operator `secret:` refs).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from auoreo.core.approvals import unattended_action, unattended_hint, unattended_kind
from auoreo.core.permissions import SENSITIVE_NOTE

if TYPE_CHECKING:
    from auoreo.core.permissions import PermissionEngine
    from auoreo.gateway.types import ToolCall
    from auoreo.harness.loader import ToolDef
    from auoreo.tools.context import ToolContext
    from auoreo.tools.executor import ToolExecutor, ToolOutcome


def risk_tier(tool: ToolDef) -> str:
    m = tool.manifest
    return "high" if m.is_destructive else "low" if m.is_read_only else "medium"


_REF_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
# Environment backends that read keys without declaring `requires_env` name them in their
# description ("Key E2B_API_KEY from …"); until they declare it, that text is the declaration.
_DESCRIBED_KEY = re.compile(r"\b[A-Z][A-Z0-9_]*(?:API_KEY|TOKEN_ID|TOKEN_SECRET|TOKEN|SECRET)\b")


def _operator_refs(node: Any, key: str = "") -> Iterator[str]:
    """Secret names the operator configured in settings: `secret:<NAME>` values anywhere,
    plus plain names in `*_secret` fields (e.g. `livekit.api_key_secret`)."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _operator_refs(v, str(k))
    elif isinstance(node, list | tuple):
        for v in node:
            yield from _operator_refs(v, key)
    elif isinstance(node, str):
        if node.startswith("secret:"):
            yield node[7:]
        elif key.endswith("_secret") and _REF_NAME.fullmatch(node):
            yield node


def secret_names(tool: ToolDef, settings: Any, harness: Any = None) -> list[str]:
    """A tool call's secret scope (spec §25.11): the tool's `requires_env`, operator-configured
    settings refs, and the `requires_env` of environment/selected backends the call may use.
    Anything else (another provider's key, a different tool's token) is refused."""
    names = set(tool.manifest.requires_env)
    dump = getattr(settings, "model_dump", None)
    if callable(dump):
        names.update(_operator_refs(dump(mode="json")))
    selected = set((getattr(settings, "backends", None) or {}).values())
    snap = getattr(harness, "snapshot", None)
    for bdef in (getattr(snap, "backends", None) or {}).values():
        m = bdef.manifest
        if m.kind == "environment" or m.name in selected or m.default:
            names.update(m.requires_env)
        if m.kind == "environment":
            names.update(_DESCRIBED_KEY.findall(m.description))
    return sorted(names)


async def unattended_gate(
    ex: ToolExecutor,
    call: ToolCall,
    tool: ToolDef,
    ctx: ToolContext,
    permission: str,
    pattern: str,
    audit: dict[str, Any],
    reason: str,
) -> ToolOutcome | None:
    """None = proceed (auto-approved by policy); else the refusal outcome. Sensitive writes
    and sandbox escalations never auto-approve (fail closed)."""
    kind = await unattended_kind(ex.s, ctx.session_id, ctx.services.get("unattended"))
    closed = SENSITIVE_NOTE in reason or permission == "sandbox_escalate"
    if unattended_action(ex.s.settings, kind) == "approve" and not closed:
        await ex.s.audit.record(
            actor="policy",
            action="permission.unattended",
            session_id=ctx.session_id,
            decision="allow",
            reason=f"unattended:{kind}",
            detail=audit,
        )
        return None
    why = "never auto-approved" if closed else f"approvals.unattended.{kind} is 'deny'"
    hint = "" if closed else f" or run {unattended_hint(kind)}"
    return ex._err(
        call,
        f"'{tool.name}' needs approval for `{pattern[:200]}` but this run is non-interactive "
        f"({kind}; {why}); allow it with a permission rule{hint}",
    )


async def external_dir_gate(
    ex: ToolExecutor,
    call: ToolCall,
    tool: ToolDef,
    args: dict[str, Any],
    ctx: ToolContext,
    engine: PermissionEngine,
) -> ToolOutcome | None:
    """§19.6: `read`/`edit` tools with a `path` outside cwd ask `external_directory` for the
    parent directory (`<dir>/*`, so an `always` grant covers its files)."""
    m = tool.manifest
    if m.pattern_arg != "path" or m.permission_name not in ("read", "edit"):
        return None
    if not isinstance(args.get("path"), str):
        return None
    path = ctx.resolve_path(args["path"])
    decision = engine.external_directory(path, ctx.cwd, interactive=ctx.interactive)
    if decision is None or decision.action == "allow":
        return None
    pattern = f"{path.parent}/*"
    audit = {
        "tool": tool.name,
        "permission": "external_directory",
        "pattern": pattern[:500],
        "call_id": call.id,
    }
    if decision.action == "deny":
        await ex.s.audit.record(
            actor="agent",
            action="tool.denied",
            session_id=ctx.session_id,
            decision="deny",
            reason=decision.detail,
            detail=audit,
        )
        return ex._err(call, f"permission denied ({decision.detail}) for {path}. Do not retry unchanged.")
    refused, _ = await ex._ask(
        call, tool, args, ctx, engine, "external_directory", pattern, audit, decision.detail
    )
    return refused
