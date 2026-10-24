from auoreo.core.redaction import PREFIXES
from auoreo.memory.files import memory_dir
from auoreo.tools.context import ToolContext, ToolError

FILES = {"user": ("USER.md", "user_char_limit"), "memory": ("MEMORY.md", "memory_char_limit")}


def _check_safe(text: str) -> None:
    if any(p in text for p in PREFIXES if len(p) > 3) or "PRIVATE KEY" in text:
        raise ToolError("refusing to store something that looks like a secret")


def _edit(text: str, op: dict) -> str:
    action, content, old = op["action"], op.get("content", ""), op.get("old_text")
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


async def run(args: dict, ctx: ToolContext) -> str:
    mem = ctx.services.get("memory")
    services = ctx.services.get("services")
    ops = args["operations"]
    for op in ops:
        _check_safe(op.get("content", ""))
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
    results: list[str] = []
    fact_ops = [op for op in ops if op["target"] == "fact"]
    if fact_ops and mem is None:
        raise ToolError("memory store is disabled")
    for op in fact_ops:
        if op["action"] == "add":
            fid, created = await mem.add_fact(op.get("content", ""), category="agent")
            results.append(f"fact {fid} {'added' if created else 'already known'}")
            continue
        matches = await mem.find_facts(op.get("old_text", "") or "\0")
        if len(matches) != 1:
            preview = "; ".join(t[:80] for _, t in matches[:5]) or "none"
            raise ToolError(f"old_text matched {len(matches)} facts ({preview})")
        fid = matches[0][0]
        ok = await (
            mem.replace_fact(fid, op.get("content", ""))
            if op["action"] == "replace"
            else mem.remove_fact(fid)
        )
        results.append(f"fact {fid} {op['action']}d" if ok else f"fact {fid} unchanged")
    for name, text in staged.items():
        (mdir / name).write_text(text, encoding="utf-8")
        results.append(f"{name} updated ({len(text)} chars)")
    return "saved — do not repeat these operations:\n" + "\n".join(results)
