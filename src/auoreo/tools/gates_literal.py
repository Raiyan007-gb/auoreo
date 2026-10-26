"""H1b: the literal-secret gate (spec §25.10) the executor runs after the permission decision.

A shell-text argument holding a credential (a stored secret's value or a high-confidence key
shape, `core.floors_literal`) always goes to a human: no rule, mode (yolo/acceptEdits),
smart-mode guardian or PermissionRequest hook can allow it. The approval card shows the
command with the span masked plus the "move it to secrets.db" hint; the grant is one-shot
(never `always`). Without a human it is denied. Audit rows carry the masked text only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from auoreo.core import floors_literal
from auoreo.tools.shell_floor import shell_texts

NOTE = floors_literal.NOTE

if TYPE_CHECKING:
    from auoreo.gateway.types import ToolCall
    from auoreo.harness.loader import ToolDef
    from auoreo.tools.context import ToolContext
    from auoreo.tools.executor import ToolExecutor, ToolOutcome


def scan(ex: ToolExecutor, tool: ToolDef, args: dict[str, Any]) -> dict[str, str] | None:
    """`{arg: masked text}` for every shell-text argument with a literal secret, else None."""
    texts = shell_texts(tool, args)
    if not texts:
        return None
    store = getattr(ex.s, "secrets", None)
    known = store.values() if store is not None and hasattr(store, "values") else []
    min_len = ex.s.settings.h1b.literal_secret_min_len
    hits: dict[str, str] = {}
    for name, text in texts:
        spans = floors_literal.find(text, known, min_len=min_len)
        if spans:
            hits[name] = floors_literal.masked(text, spans)
    return hits or None


def mask_pattern(pattern: str, hits: dict[str, str], tool: ToolDef, args: dict[str, Any]) -> str:
    """The permission pattern with the secret masked (the pattern is usually the command)."""
    for name, masked_text in hits.items():
        raw = args.get(name)
        if isinstance(raw, str) and raw:
            pattern = pattern.replace(raw, masked_text)
    store_free = floors_literal.find(pattern)
    return floors_literal.masked(pattern, store_free) if store_free else pattern


async def gate(
    ex: ToolExecutor,
    call: ToolCall,
    tool: ToolDef,
    ctx: ToolContext,
    permission: str,
    shown: str,
    hits: dict[str, str],
    audit: dict[str, Any],
) -> ToolOutcome | None:
    """Ask the human (masked card, one-shot); deny without one. None = approved."""
    detail = f"{floors_literal.HINT}\n\n" + "\n".join(f"{k}: {v}" for k, v in hits.items())
    audit = {**audit, "pattern": shown[:500], "floor": "literal_secret"}
    if not ctx.interactive:
        await ex.s.audit.record(
            actor="agent",
            action="tool.denied",
            session_id=ctx.session_id,
            decision="deny",
            reason=f"floor: {floors_literal.NOTE}",
            detail=audit,
        )
        return ex._err(call, f"permission denied ({floors_literal.NOTE}): {floors_literal.HINT}.")
    outcome = await ex.s.approvals.request(
        ctx.session_id,
        tool.name,
        permission,
        shown,
        detail,
        cwd=str(ctx.cwd),
        risk="high",
        one_shot=True,
    )
    await ex.s.audit.record(
        actor="human",
        action="permission.reply",
        session_id=ctx.session_id,
        decision=outcome.reply or "timeout",
        reason=f"floor: {floors_literal.NOTE}",
        detail={**audit, "request_id": outcome.request_id},
    )
    if outcome.granted:
        return None
    from auoreo.tools.context import ToolResult
    from auoreo.tools.executor import ToolOutcome

    if outcome.message:
        return ex._err(call, f"user rejected this call with feedback: {outcome.message}")
    return ToolOutcome(
        call, ToolResult(f"user rejected this tool call ({floors_literal.HINT})", is_error=True), halt=True
    )
