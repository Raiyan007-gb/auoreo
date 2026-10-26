"""S7 post-compaction restore (spec §11.9).

After the middle of a conversation is replaced, the agent loses the file bodies and skill
instructions it had in context. This module rebuilds a bounded `<restored-context>` block
that rides inside the compaction-summary message:

- ≤ 5 most recently touched files from the compacted span (re-read from disk, ≤ 5K chars
  each, ≤ 50K total), skipping files the kept tail already shows and secret paths;
- skills loaded in that span (`skill` tool results, ≤ 5K each, ≤ 25K total);
- plan mode / plan steps / todos;
- session metadata (id, title, agent, workflow, model, cwd), capped at 16 KB.

It also owns the session-memory notes file (`<home>/sessions/<id>/session-memory.md`) used
by the LLM-free session-memory compaction, and re-runs SessionStart hooks after a compaction.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from auoreo.runtime.compaction_plan import tool_calls_in
from auoreo.runtime.hooks import hooks_of

if TYPE_CHECKING:
    from auoreo.core.services import Services
    from auoreo.runtime.runner import SessionRuntime

log = logging.getLogger(__name__)

SESSION_MEMORY_FILE = "session-memory.md"
SESSION_MEMORY_MAX_CHARS = 160_000
FILE_TOOLS = frozenset({"read", "write", "edit", "str_replace"})
SKILL_TOOL = "skill"
_SECRET = re.compile(r"(^|/)(\.env(\.[^/]*)?|secrets\.db)$")


def session_memory_path(home: Path, session_id: str) -> Path:
    return home / "sessions" / session_id / SESSION_MEMORY_FILE


def read_session_memory(home: Path, session_id: str) -> str | None:
    """Session notes maintained outside the summarizer (a hook, a background extractor, the
    user). Empty or missing → None (the LLM summarizer runs instead)."""
    path = session_memory_path(home, session_id)
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return text[:SESSION_MEMORY_MAX_CHARS] or None


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}\n… [truncated {len(text) - limit} chars]"


def _paths(calls: list[tuple[str, dict[str, Any], str | None]]) -> list[str]:
    out: list[str] = []
    for name, args, _ in calls:
        path = args.get("path") if name in FILE_TOOLS else None
        if isinstance(path, str) and path:
            if path in out:
                out.remove(path)
            out.append(path)  # most recently touched last
    return out


def _resolve(cwd: Path, raw: str) -> Path | None:
    """Inside the working directory, not a secret, an existing regular file."""
    if _SECRET.search(raw.replace("\\", "/")):
        return None
    try:
        path = (cwd / raw).resolve()
        path.relative_to(cwd.resolve())
    except (OSError, ValueError):
        return None
    if _SECRET.search(path.as_posix()) or not path.is_file():
        return None
    return path


def restore_files(services: Services, middle: list[dict[str, Any]], tail: list[dict[str, Any]]) -> list[str]:
    cfg = services.settings.compaction
    if cfg.restore_files <= 0:
        return []
    shown = set(_paths(tool_calls_in(tail)))
    blocks: list[str] = []
    used = 0
    for raw in reversed([p for p in _paths(tool_calls_in(middle)) if p not in shown]):  # newest first
        path = _resolve(services.cwd, raw)
        if path is None:
            continue
        if len(blocks) >= cfg.restore_files:
            break
        try:
            body = _clip(path.read_text(encoding="utf-8", errors="replace"), cfg.restore_file_chars)
        except OSError:
            continue
        if used + len(body) > cfg.restore_files_total_chars:
            break
        used += len(body)
        blocks.append(f'<file path="{raw}">\n{body}\n</file>')
    return list(reversed(blocks))


def restore_skills(services: Services, middle: list[dict[str, Any]], tail: list[dict[str, Any]]) -> list[str]:
    cfg = services.settings.compaction
    shown = {str(a.get("name")) for n, a, _ in tool_calls_in(tail) if n == SKILL_TOOL}
    latest: dict[str, str] = {}
    for name, args, result in tool_calls_in(middle):
        skill = args.get("name")
        if name == SKILL_TOOL and isinstance(skill, str) and result and skill not in shown:
            latest.pop(skill, None)
            latest[skill] = result
    blocks: list[str] = []
    used = 0
    for skill, body in reversed(latest.items()):
        text = _clip(body, cfg.restore_skill_chars)
        if used + len(text) > cfg.restore_skills_total_chars:
            break
        used += len(text)
        blocks.append(f'<skill name="{skill}">\n{text}\n</skill>')
    return list(reversed(blocks))


def restore_mode(rt: SessionRuntime) -> list[str]:
    st = rt.state
    parts: list[str] = []
    if st.plan_mode:
        parts.append("<plan-mode>on: plan only; do not edit files until the plan is approved</plan-mode>")
    if st.plan:
        parts.append(f"<plan>\n{json.dumps(st.plan, ensure_ascii=False)[:4000]}\n</plan>")
    if st.todos:
        parts.append(f"<todos>\n{json.dumps(st.todos, ensure_ascii=False)[:4000]}\n</todos>")
    return parts


async def session_metadata(services: Services, rt: SessionRuntime) -> str:
    row = await services.get_session(rt.session_id)
    meta = {
        "session_id": rt.session_id,
        "title": row.title if row else "",
        "agent": rt.agent,
        "workflow": rt.workflow,
        "model": rt.model,
        "cwd": str(services.cwd),
    }
    text = "\n".join(f"{k}: {v}" for k, v in meta.items() if v not in (None, ""))
    return f"<session-metadata>\n{text[: services.settings.compaction.metadata_chars]}\n</session-metadata>"


async def restore_block(
    services: Services, rt: SessionRuntime, middle: list[dict[str, Any]], tail: list[dict[str, Any]]
) -> str:
    """The bounded `<restored-context>` block (never raises; a failure restores less)."""
    parts: list[str] = []
    try:
        parts.append(await session_metadata(services, rt))
        parts += restore_files(services, middle, tail)
        parts += restore_skills(services, middle, tail)
        parts += restore_mode(rt)
    except Exception:
        log.exception("post-compaction restore failed")
    if not parts:
        return ""
    return "<restored-context>\n" + "\n".join(parts) + "\n</restored-context>"


async def rerun_session_start(services: Services, rt: SessionRuntime, compaction_id: str) -> None:
    """S7: SessionStart hooks run again (`source: compact`) so their side effects and any
    context they provide are re-established after the history changed. Best effort."""
    hooks = hooks_of(services)
    if hooks is None or not services.settings.compaction.rerun_session_start:
        return
    payload = {
        "agent": rt.agent,
        "workflow": rt.workflow,
        "cwd": str(services.cwd),
        "source": "compact",
        "compaction_id": compaction_id,
    }
    try:
        await hooks.run("SessionStart", session_id=rt.session_id, payload=payload, match_key=rt.agent)
    except Exception:
        log.exception("SessionStart hooks (post-compaction) failed")


__all__ = [
    "SESSION_MEMORY_FILE",
    "read_session_memory",
    "rerun_session_start",
    "restore_block",
    "session_memory_path",
]
