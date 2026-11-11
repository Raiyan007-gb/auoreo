from datetime import UTC, datetime

from auoreo.core.redaction import PREFIXES
from auoreo.memory.files import memory_dir
from auoreo.memory.prefs import PrefsError, inbox
from auoreo.memory.recall import TOPICS_REFUSED, tenant_of
from auoreo.memory.topics import (
    TYPES,
    DriftError,
    drop_index,
    guarded_write,
    parse_topic,
    render_topic,
    topic_path,
    upsert_index,
)
from auoreo.tools.context import ToolContext, ToolError

FILES = {"user": ("USER.md", "user_char_limit"), "memory": ("MEMORY.md", "memory_char_limit")}
FILE_ACTIONS = {"add", "replace", "remove"}
SCOPE_FOR_TYPE = {"user": "user", "feedback": "user", "project": "project", "reference": "team"}
FAILURES_KEY = "_memory_failures"
DEFAULT_FAILURE_CAP = 3


def _check_safe(text: str) -> None:
    if any(p in text for p in PREFIXES if len(p) > 3) or "PRIVATE KEY" in text:
        raise ToolError("refusing to store something that looks like a secret")


def _entries(text: str) -> list[str]:
    return [ln[2:82] for ln in text.splitlines() if ln.startswith("- ")][:20]


def _miss(what: str, count: int, entries: list[str]) -> ToolError:
    listing = "\n".join(f"- {e}" for e in entries) or "- (none)"
    label = "current_entries" if count == 0 else "matching entries"
    return ToolError(f"old_text matched {count} {what}; it must match exactly one. {label}:\n{listing}")


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
        matching = [e for e in _entries(text) if old in e] if count else _entries(text)
        raise _miss("entries", count, matching)
    if action == "replace":
        return text.replace(old, content.strip())
    start = text.index(old)
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", start + len(old))
    return text[:line_start] + (text[line_end + 1 :] if line_end != -1 else "")


def _type(op: dict) -> str:
    t = op.get("type") or "project"
    if t not in TYPES:
        raise ToolError(f"type must be one of {', '.join(TYPES)}")
    return t


def _expires(op: dict) -> float | None:
    raw = op.get("expires_at")
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()
    except ValueError:
        raise ToolError("expires_at must be a YYYY-MM-DD date") from None


async def _resolve(mem, op: dict) -> int | None:
    """Validate a fact op before anything is written; returns the matched fact id."""
    if op["action"] == "add":
        if not op.get("content", "").strip():
            raise ToolError("add needs content")
        _type(op)
        _expires(op)
        return None
    if op["action"] == "replace" and not op.get("content", "").strip():
        raise ToolError("replace needs content")
    if op["action"] == "feedback" and "helpful" not in op:
        raise ToolError("feedback needs helpful: true|false")
    matches = await mem.find_facts(op.get("old_text", "") or "\0")
    if len(matches) != 1:
        shown = matches[:5] if matches else (await mem.find_facts(""))[-10:]
        raise _miss("facts", len(matches), [f"fact {i}: {t[:80]}" for i, t in shown])
    return int(matches[0][0])


async def _apply(mem, op: dict, fid: int | None, ctx: ToolContext) -> str:
    action = op["action"]
    if fid is None:
        t = _type(op)
        scope = op.get("scope") or SCOPE_FOR_TYPE[t]
        new_id, created = await mem.add_fact(
            op.get("content", ""),
            category=t,
            source="agent",
            scope=scope,
            owner_session=ctx.session_id if scope == "session" else None,
            expires_at=_expires(op),
            half_life_days=op.get("half_life_days"),
        )
        return f"fact {new_id} {'added' if created else 'already known'} (type={t}, scope={scope})"
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


def _stage_topic(ctx: ToolContext, op: dict, staged: dict, index: str) -> str:
    """Topic ops: add (create/overwrite) / replace (old_text in the body) / remove (archive)."""
    name = op.get("name") or ""
    if not name:
        raise ToolError("target=topic needs name")
    path = topic_path(ctx.home, name)
    current = staged.get(path, path.read_text(encoding="utf-8") if path.is_file() else None)
    if op["action"] == "remove":
        if current is None:
            raise ToolError(f"topic '{name}' does not exist")
        staged[path] = None
        return drop_index(index, name)
    if op["action"] not in {"add", "replace"}:
        raise ToolError(f"{op['action']} does not apply to target=topic")
    description = op.get("description") or (op.get("content", "").strip().splitlines() or [name])[0]
    if op["action"] == "replace":
        if current is None or not op.get("old_text") or current.count(op["old_text"]) != 1:
            raise ToolError("replace on a topic needs old_text matching exactly once in that topic")
        body = current.split("\n---\n", 1)[-1].replace(op["old_text"], op.get("content", "").strip())
        meta = parse_topic(current)
        staged[path] = render_topic(
            meta["name"], op.get("description") or meta.get("description", ""), meta["type"], body
        )
        return upsert_index(index, meta["name"], op.get("description") or meta.get("description", ""))
    if not op.get("content", "").strip():
        raise ToolError("add needs content")
    staged[path] = render_topic(name, description, _type(op), op["content"])
    return upsert_index(index, name, description)


def _usage(name: str, text: str, limit: int | None) -> str:
    if not limit:
        return f"{name} updated ({len(text)} chars)"
    return f"{name} updated ({len(text)}/{limit} chars, {round(100 * len(text) / limit)}% used)"


async def _run(args: dict, ctx: ToolContext) -> str:
    mem = ctx.services.get("memory")
    services = ctx.services.get("services")
    ops = args["operations"]
    if any(op.get("target") == "topic" for op in ops) and tenant_of(ctx.services):
        raise ToolError(TOPICS_REFUSED)
    for op in ops:
        _check_safe(op.get("content", "") + op.get("description", ""))
    prefs_ops = [op for op in ops if op["target"] == "prefs"]
    ops = [op for op in ops if op["target"] != "prefs"]
    if not ops:
        return "proposed — do not repeat these operations:\n" + "\n".join(
            await _propose_prefs(prefs_ops, ctx, services)
        )
    # Files: compute every change first, enforce the final budget, then write (atomic batch).
    mdir = memory_dir(ctx.home)
    staged: dict[str, str] = {}
    topics: dict = {}
    for op in ops:
        if op["target"] in FILES:
            name, _ = FILES[op["target"]]
            current = staged.get(
                name, (mdir / name).read_text(encoding="utf-8") if (mdir / name).exists() else ""
            )
            staged[name] = _edit(current, op)
        elif op["target"] == "topic":
            index = staged.get(
                "MEMORY.md",
                (mdir / "MEMORY.md").read_text(encoding="utf-8") if (mdir / "MEMORY.md").exists() else "",
            )
            staged["MEMORY.md"] = _stage_topic(ctx, op, topics, index)
    limits: dict[str, int] = {}
    if services is not None:
        for name, text in staged.items():
            limit_key = next(k for n, k in FILES.values() if n == name)
            limit = limits[name] = getattr(services.settings.memory, limit_key)
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
    results = [await _apply(mem, op, fid, ctx) for op, fid in zip(fact_ops, resolved, strict=True)]
    try:
        for path, text in topics.items():
            if text is None:
                archive = mdir / ".archive" / "topics"
                archive.mkdir(parents=True, exist_ok=True)
                path.replace(archive / path.name)
                results.append(f"topic {path.stem} archived")
                continue
            bak = guarded_write(path, text)
            results.append(
                f"topics/{path.name} written" + (f" (previous edit saved as {bak.name})" if bak else "")
            )
        for name, text in staged.items():
            bak = guarded_write(mdir / name, text)
            note = f" (outside edit saved as {bak.name})" if bak else ""
            results.append(_usage(name, text, limits.get(name)) + note)
    except DriftError as exc:
        raise ToolError(str(exc)) from None
    results += await _propose_prefs(prefs_ops, ctx, services)
    if fact_ops and mem is not None:
        counts = await mem._run(mem.store.counts)
        results.append(f"{counts['facts'] - counts['archived']} active facts stored")
    return "saved — do not repeat these operations:\n" + "\n".join(results)


async def run(args: dict, ctx: ToolContext) -> str:
    """≤ N failed memory calls per turn (spec §11.8), then stop-and-answer."""
    services = ctx.services.get("services")
    cap = services.settings.i4.memory.tool_failures_per_turn if services is not None else DEFAULT_FAILURE_CAP
    failures = ctx.services.setdefault(FAILURES_KEY, [0])
    if failures[0] >= cap:
        raise ToolError(f"memory failed {failures[0]} times this turn; stop retrying and answer the user now")
    try:
        return await _run(args, ctx)
    except ToolError as exc:
        failures[0] += 1
        if failures[0] >= cap:
            raise ToolError(
                f"{exc}\n(failure {failures[0]}/{cap} this turn: stop retrying and answer now)"
            ) from None
        raise
