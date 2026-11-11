"""Observation masking as a compaction source (M7 W1 Wave 2, `m7.token.masking = "compaction"`).

Inside every compaction run, before S2 pruning, old bulky tool results are replaced by a
deterministic placeholder that names the call id and a file holding the original:

1. spill first: each original goes to `<home>/sessions/<sid>/tool-results/masked/<name>.txt`
   (bytes as the history held them, so an unmask restores them exactly); a failed write keeps
   that message as it is;
2. then the `messages` rows and `rt.history` are rewritten in place, as `_prune` does
   (only tool-message dict objects are replaced, so the turn message keeps its identity);
3. a read-dedupe notice of the same turn that points at a masked `read` result is rewritten
   in the same batch (`masking_plan.stale_notices`).

The read dedupe needs no code for the other direction: it compares the live history with the
digest of what it returned, so a masked earlier result means a full read.

`/compact unmask=<call id|all>` restores spilled originals (flag on or off, so it also serves a
rollback) and emits `context.unmasked`. Rewritten notices are not restored. Spill files follow
the executor's spill files: nothing removes `<home>/sessions/<sid>/` today.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from auoreo.core import metrics_text
from auoreo.core import observability as obs
from auoreo.core.db import UsageRow
from auoreo.core.usage import cache_hit_ratio
from auoreo.runtime.masking_plan import (
    NOTICE_MASKED,
    is_placeholder,
    plan,
    spill_name,
    stale_notices,
    tool_names,
)

if TYPE_CHECKING:
    from auoreo.core.settings_m7 import M7Mask
    from auoreo.runtime.compaction import CompactionService
    from auoreo.runtime.runner import SessionRuntime

log = logging.getLogger(__name__)
METRIC = "context_masked_tokens_total"
obs.METRICS.setdefault(METRIC, ("counter", frozenset()))
metrics_text.HELP.setdefault(METRIC, "Estimated prompt tokens removed by observation masking.")
MAX_EVENT_IDS = 200


@dataclass
class Batch:
    changes: dict[int, dict[str, Any]] = field(default_factory=dict)
    call_ids: list[str] = field(default_factory=list)
    tokens: int = 0
    suffix_tokens: int = 0


def enabled(comp: CompactionService) -> bool:
    return comp.s.settings.m7.token.masking == "compaction"


def folders(home: Path, session_id: str) -> tuple[Path, Path]:
    """`(masked spill folder, executor spill folder)` of a session."""
    results = home / "sessions" / session_id / "tool-results"
    return results / "masked", results


def write_spill(path: Path, text: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))  # bytes: no newline translation on Windows
    except OSError:
        log.warning("masking: could not spill a tool result; it stays in context", exc_info=True)
        return False
    return True


def placeholder(tool: str, call_id: str, content: str, path: Path) -> str:
    lines = content.count("\n") + 1
    return (
        f"[masked: {tool} result of call {call_id}, {len(content)} chars, {lines} lines. "
        f"Original: {path}. Run the tool again or read that file if you need it.]"
    )


def mask_batch(history: list[dict[str, Any]], cfg: M7Mask, folder: Path, executor: Path) -> Batch:
    """Plan, spill and build the replacement messages (nothing in `history` changes here)."""
    p = plan(history, cfg)
    names = tool_names(history)
    out = Batch(suffix_tokens=p.suffix_tokens)
    reads: list[int] = []
    for i in p.indices:
        m = history[i]
        call_id, content = str(m["tool_call_id"]), str(m["content"])
        name = spill_name(call_id)
        path = folder / f"{name}.txt"
        if not write_spill(path, content):
            continue
        full = executor / f"{call_id}.txt"
        shown = full if name == call_id and full.is_file() else path
        new = {**m, "content": placeholder(names.get(call_id, "tool"), call_id, content, shown)}
        out.changes[i] = new
        out.call_ids.append(call_id)
        out.tokens += max(0, len(content) - len(new["content"])) // 4
        if names.get(call_id) == "read":
            reads.append(i)
    for j in stale_notices(history, reads):
        out.changes[j] = {**history[j], "content": NOTICE_MASKED}
    return out


async def _write_rows(
    comp: CompactionService, rt: SessionRuntime, changes: dict[int, dict[str, Any]]
) -> None:
    async with comp.s.db.session() as s:
        rows = await comp._rows(s, rt)
        for i, m in changes.items():
            rows[i].message = m
        await s.commit()
    for i, m in changes.items():
        rt.history[i] = m


async def _hit_ratio(comp: CompactionService, session_id: str) -> float | None:
    """`h` of the session's last main-turn call (None when there is none)."""
    async with comp.s.db.session() as s:
        q = (
            select(UsageRow)
            .where(UsageRow.session_id == session_id, UsageRow.outcome == "ok")
            .order_by(UsageRow.id.desc())
            .limit(1)
        )
        row = (await s.execute(q)).scalars().first()
    return None if row is None else round(cache_hit_ratio(row.cached_tokens or 0, row.prompt_tokens or 0), 4)


async def apply(comp: CompactionService, rt: SessionRuntime, cid: str, trigger: str) -> int:
    """Mask one batch; returns the number of rewritten messages (0 with the flag off)."""
    if not enabled(comp):
        return 0
    folder, executor = folders(comp.s.settings.home, rt.session_id)
    batch = mask_batch(rt.history, comp.s.settings.m7.token.mask, folder, executor)
    if not batch.call_ids:
        return 0
    await _write_rows(comp, rt, batch.changes)
    data = {
        "compaction_id": cid,
        "trigger": trigger,
        "call_ids": batch.call_ids[:MAX_EVENT_IDS],
        "count": len(batch.call_ids),
        "tokens": batch.tokens,
        "suffix_tokens": batch.suffix_tokens,
        "hit_ratio": await _hit_ratio(comp, rt.session_id),
        "gate": "pressure",  # inside a compaction run; `economic` / `cold` are not built
    }
    await comp.s.emit(rt.session_id, "context.masked", data)
    obs.count(METRIC, batch.tokens)
    return len(batch.changes)


async def unmask(comp: CompactionService, rt: SessionRuntime, target: str) -> list[str]:
    """Restore spilled originals of `target` (a call id or `all`); returns the restored ids."""
    folder, _ = folders(comp.s.settings.home, rt.session_id)
    changes: dict[int, dict[str, Any]] = {}
    restored: list[str] = []
    for i, m in enumerate(rt.history):
        call_id = m.get("tool_call_id")
        if m.get("role") != "tool" or not isinstance(call_id, str) or not is_placeholder(m):
            continue
        if target not in ("all", call_id):
            continue
        try:
            text = (folder / f"{spill_name(call_id)}.txt").read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # no spill (e.g. a rewritten notice): nothing to restore
        changes[i] = {**m, "content": text}
        restored.append(call_id)
    if changes:
        await _write_rows(comp, rt, changes)
        comp.post.mark_post_compaction(rt.session_id, f"unmask_{uuid.uuid4().hex[:12]}")
        await comp.s.emit(
            rt.session_id, "context.unmasked", {"call_ids": restored[:MAX_EVENT_IDS], "count": len(restored)}
        )
    return restored


__all__ = ["Batch", "apply", "enabled", "mask_batch", "placeholder", "unmask", "write_spill"]
