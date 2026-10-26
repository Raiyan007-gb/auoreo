from auoreo.core.redaction import PREFIXES
from auoreo.memory.files import memory_dir
from auoreo.memory.prefs import PrefsError, inbox
from auoreo.tools.context import ToolContext, ToolError

FILES = {"user": ("USER.md", "user_char_limit"), "memory": ("MEMORY.md", "memory_char_limit")}
FILE_ACTIONS = {"add", "replace", "remove"}


def _check_safe(text: str) -> None:
    if any(p in text for p in PREFIXES if len(p) > 3) or "PRIVATE KEY" in text:
        raise ToolError("refusing to store something that looks like a secret")


def _edit(text: str, op: dict) -> str:
    action, content, old = op["action"], op.get("content", ""), op.get("old_text")
    if action not in FILE_ACTIONS:
        raise ToolError(f"{action} only applies to target=fact")
    if action == "add":
        if not content:
            raise ToolError("add needs content")
        return text.rstrip("\n") + f"\n- {content.strip()}\n"
    if not old:
        raise ToolError(f"{action} needs old_text")
    count = text.count(old)
    if count != 1:
        raise ToolError(f"old_text matches {count} entries; it must match exactly one")
    if action == "replace":
        return text.replace(old, content.strip())
    start = text.index(old)
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", start + len(old))
    return text[:line_start] + (text[line_end + 1 :] if line_end != -1 else "")


async def _resolve(mem, op: dict) -> int | None:
    """Validate a fact op before anything is written; returns the matched fact id."""
    if op["action"] == "add":
        if not op.get("content", "").strip():
            raise ToolError("add needs content")
        return None
    if op["action"] == "replace" and not op.get("content", "").strip():
        raise ToolError("replace needs content")
    if op["action"] == "feedback" and "helpful" not in op:
        raise ToolError("feedback needs helpful: true|false")
    matches = await mem.find_facts(op.get("old_text", "") or "\0")
    if len(matches) != 1:
        preview = "; ".join(t[:80] for _, t in matches[:5]) or "none"
        raise ToolError(f"old_text matched {len(matches)} facts ({preview}); it must match exactly one")
    return int(matches[0][0])


async def _apply(mem, op: dict, fid: int | None) -> str:
    action = op["action"]
    if fid is None:
        new_id, created = await mem.add_fact(op.get("content", ""), category="agent", source="agent")
        return f"fact {new_id} {'added' if created else 'already known'}"
    if action == "replace":
        new_id = await mem.replace_fact(fid, op.get("content", ""))
        return f"fact {fid} superseded by fact {new_id}" if new_id else f"fact {fid} unchanged"
    if action in {"remove", "forget"}:  # facts are archived, never deleted, from a tool
        ok = await mem.forget(fid, actor="agent", reason=op.get("content") or "forgotten by the agent")
        return f"fact {fid} archived" if ok else f"fact {fid} unchanged"
    if action in {"pin", "unpin"}:
        ok = await mem.pin(fid, action == "pin", actor="agent")
        return f"fact {fid} {action}ned" if ok else f"fact {fid} unchanged"
    trust = await mem.feedback(fid, bool(op.get("helpful")))
    return f"fact {fid} trust now {trust:.2f}" if trust is not None else f"fact {fid} unchanged"


def _box(ctx: ToolContext, services: object):
    settings = getattr(services, "settings", None)
    return inbox(settings) if settings is not None else inbox(_HomeOnly(ctx.home))


def _validate_prefs(ops: list[dict], ctx: ToolContext, services: object) -> None:
    box = _box(ctx, services)
    for op in ops:  # validate the whole batch before anything is written or queued
        if op["action"] not in {"add", "replace", "remove"}:
            raise ToolError(f"{op['action']} does not apply to target=prefs (use add, replace or remove)")
        try:
            box.validate(op["action"], op.get("content", ""), op.get("old_text"))
        except PrefsError as exc:
            raise ToolError(str(exc)) from None


async def _propose_prefs(ops: list[dict], ctx: ToolContext, services: object) -> list[str]:
    """`target: prefs` never writes PREFS.md: every op becomes a pending proposal (G6)."""
    if not ops:
        return []
    _validate_prefs(ops, ctx, services)
    box = _box(ctx, services)
    out = []
    for op in ops:
        prop = box.propose(
            op["action"],
            op.get("content", ""),
            old_text=op.get("old_text"),
            reason=op.get("reason", ""),
            session_id=ctx.session_id,
        )
        await ctx.emit("prefs.proposed", {"proposal_id": prop.id, "action": prop.action, "actor": "agent"})
        out.append(
            f"PREFS.md proposal {prop.id} queued ({prop.action}); it applies only after the user approves it"
        )
    return out


class _HomeOnly:
    def __init__(self, home: object) -> None:
        self.home = home


async def run(args: dict, ctx: ToolContext) -> str:
    mem = ctx.services.get("memory")
    services = ctx.services.get("services")
    ops = args["operations"]
    for op in ops:
        _check_safe(op.get("content", ""))
    prefs_ops = [op for op in ops if op["target"] == "prefs"]
    ops = [op for op in ops if op["target"] != "prefs"]
    if not ops:
        return "proposed — do not repeat these operations:\n" + "\n".join(
            await _propose_prefs(prefs_ops, ctx, services)
        )
    # Files: compute every change first, enforce the final budget, then write (atomic batch).
    mdir = memory_dir(ctx.home)
    staged: dict[str, str] = {}
    for op in ops:
        if op["target"] in FILES:
            name, limit_key = FILES[op["target"]]
            current = staged.get(
                name, (mdir / name).read_text(encoding="utf-8") if (mdir / name).exists() else ""
            )
            staged[name] = _edit(current, op)
    if services is not None:
        for name, text in staged.items():
            limit_key = next(k for n, k in FILES.values() if n == name)
            limit = getattr(services.settings.memory, limit_key)
            if len(text) > limit:
                raise ToolError(
                    f"{name} would be {len(text)} chars (limit {limit}); "
                    "consolidate entries instead of adding"
                )
    fact_ops = [op for op in ops if op["target"] == "fact"]
    if fact_ops and mem is None:
        raise ToolError("memory store is disabled")
    resolved = [await _resolve(mem, op) for op in fact_ops]  # all-or-nothing: validate first
    _validate_prefs(prefs_ops, ctx, services)
    results = [await _apply(mem, op, fid) for op, fid in zip(fact_ops, resolved, strict=True)]
    for name, text in staged.items():
        (mdir / name).write_text(text, encoding="utf-8")
        results.append(f"{name} updated ({len(text)} chars)")
    results += await _propose_prefs(prefs_ops, ctx, services)
    return "saved — do not repeat these operations:\n" + "\n".join(results)
