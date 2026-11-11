"""I5: approve a tool call with edited arguments (spec §5.2 `allow{updatedInput}`).

The approval card shows the call's arguments (redacted, size-capped) in an editor. When the
human answers `allow` with `updated_input`, the edit is NOT trusted blindly: it must pass
the tool's JSON Schema again, the handler's `pre_check` floor, the shell floor and the
permission engine's `deny` verdict for the new pattern (floors stay unbypassable — a human
cannot edit a call into something no rule could ever allow). The accepted edit replaces the
dispatched arguments in place and is audited as `tool.input_edited` (key names + hashes,
never the values).
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from auoreo.core.redaction import redact
from auoreo.tools.args import InvalidArgumentsError, validate_args
from auoreo.tools.stages import policy_context, pre_check

if TYPE_CHECKING:
    from auoreo.core.permissions import PermissionEngine
    from auoreo.gateway.types import ToolCall
    from auoreo.harness.loader import ToolDef
    from auoreo.tools.context import ToolContext
    from auoreo.tools.executor import ToolExecutor, ToolOutcome


def _cap(settings: Any) -> int:
    cfg = getattr(getattr(settings, "i5", None), "approval_edit", None)
    return int(getattr(cfg, "max_input_bytes", 64 * 1024))


def editable(settings: Any) -> bool:
    cfg = getattr(getattr(settings, "i5", None), "approval_edit", None)
    return bool(getattr(cfg, "enabled", True))


def card_input(args: dict[str, Any], settings: Any) -> dict[str, Any] | None:
    """The arguments for the card's editor: redacted, or None when too large to edit."""
    text = json.dumps(args, ensure_ascii=False)
    if len(text.encode()) > _cap(settings):
        return None
    try:
        out = json.loads(redact(text))
    except ValueError:  # redaction split a JSON token: show nothing rather than garbage
        return None
    return out if isinstance(out, dict) else None


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


def changed_keys(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return sorted(k for k in {*before, *after} if before.get(k, object()) != after.get(k, object()))


async def apply_edit(
    ex: ToolExecutor,
    call: ToolCall,
    tool: ToolDef,
    args: dict[str, Any],
    ctx: ToolContext,
    engine: PermissionEngine,
    edited: dict[str, Any],
    audit: dict[str, Any],
) -> ToolOutcome | None:
    """Validate `edited` and swap it into `args` (in place). Returns an error outcome when the
    edit is refused (the call then does not run)."""
    from auoreo.tools import shell_floor

    def refuse(why: str) -> ToolOutcome:
        msg = "the human approved this call with edited input, but the edit was refused"
        return ex._err(call, f"{msg}: {why}")

    if len(json.dumps(edited, ensure_ascii=False).encode()) > _cap(ex.s.settings):
        return refuse("edited input is too large")
    try:
        validate_args(tool.name, edited, tool.manifest.parameters)
    except InvalidArgumentsError as exc:
        return refuse(exc.detail)
    floor = pre_check(tool, edited)
    if floor is None:
        limit = ex.s.settings.floors.max_inline_command_bytes
        shell = shell_floor.check(tool, edited, ctx, engine, home=ex.s.settings.home, limit=limit)
        floor = shell[1] if shell else None
    if floor is None:
        pattern = ex._pattern(tool, edited, ctx)
        decision = engine.evaluate(
            tool.manifest.permission_name,
            pattern,
            tool.manifest.default_action,
            interactive=ctx.interactive,
            context=policy_context(tool, ctx, edited),
        )
        floor = f"permission denied ({decision.detail})" if decision.action == "deny" else None
    keys = changed_keys(args, edited)
    await ex.s.audit.record(
        actor="human",
        action="tool.input_edited",
        session_id=ctx.session_id,
        decision="deny" if floor else "allow",
        reason=floor[:300] if floor else "approved with edited input",
        detail={
            **audit,
            "changed": keys,
            "before_sha": _digest(args),
            "after_sha": _digest(edited),
        },
    )
    if floor is not None:
        return refuse(floor)
    args.clear()
    args.update(edited)
    return None
