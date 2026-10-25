"""Context compaction (spec §11.9, cheapest-first subset S0, S2, S4, S5, S6, S7).

S0 meter     — thresholds from the model's context window; pressure = token count of the
               assembled request.
S2 prune     — shorten old, bulky read-type tool results in place (no LLM).
S5 summarize — one tool-free call to the `small` routing group with `prompts/compaction.md`.
S6 replace   — keep the first user message and a tail that starts at a real user message;
               swap the middle for one `<compaction-summary>` user message and move the
               replaced rows to `messages_archive`.
Triggers (S4) live in the runner: pre-step pressure, provider CONTEXT overflow, manual.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import select

from auoreo.core import observability as obs
from auoreo.core.db import MessageArchiveRow, MessageRow
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.types import LLMRequest, TextDelta, Usage
from auoreo.harness.manifests import HookEvent
from auoreo.runtime.hooks import hooks_of

if TYPE_CHECKING:
    from auoreo.core.services import Services
    from auoreo.core.usage import UsageLedger
    from auoreo.runtime.runner import SessionRuntime

log = logging.getLogger(__name__)

Trigger = Literal["pressure", "overflow", "manual"]

PRUNABLE_TOOLS = frozenset({"bash", "read", "grep", "glob", "web_fetch", "codebase_map"})
PRUNE_MIN_CHARS = 8000
PRUNE_HEAD = 4096
PRUNE_TAIL = 1024
PRUNE_KEEP_RECENT = 10
PRUNE_MIN_RECLAIM_TOKENS = 4096
TAIL_MIN_MESSAGES = 20
SUMMARY_INPUT_CHARS = 160_000
SUMMARY_MAX_TOKENS = 8192
MAX_FAILURES = 3
SUMMARY_TAG = "<compaction-summary"
WRITE_NOW = "Write the compaction summary now."


class CompactionError(Exception):
    def __init__(self, reason: str, *, tripped: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.tripped = tripped


class NothingToCompact(CompactionError):
    """Benign: every message is protected (head/tail). Not a failure, never trips the breaker."""


@dataclass(frozen=True)
class Thresholds:
    window: int
    effective: int
    auto: int
    blocking: int


@dataclass(frozen=True)
class CompactionResult:
    pre_tokens: int
    post_tokens: int
    archived: int
    compaction_id: str
    summary_chars: int
    pruned: int = 0


def thresholds(window: int, max_output: int | None = None, ratio: float | None = None) -> Thresholds:
    """`effective = window − min(max_output, 20k)`, `auto = effective − 13k`,
    `blocking = effective − 3k`. Reserves are capped at a fraction of the window so tiny
    windows (tests, local models) still get positive thresholds; unchanged for ≥ 104k."""
    reserve = min(max_output or 8192, 20_000, window // 4)
    effective = window - reserve
    auto = int(window * ratio) if ratio is not None else effective - min(13_000, window // 8)
    return Thresholds(window, effective, auto, effective - min(3_000, window // 16))


def is_real_user(m: dict[str, Any]) -> bool:
    content = m.get("content")
    return m.get("role") == "user" and not (isinstance(content, str) and content.startswith(SUMMARY_TAG))


def turn_index(history: list[dict[str, Any]], turn_msg: dict[str, Any]) -> int:
    """S7: index of this turn's user message, searched from the end by identity (indices
    move when compaction replaces the middle). Falls back to the last real user message."""
    for i in range(len(history) - 1, -1, -1):
        if history[i] is turn_msg:
            return i
    return next((i for i in range(len(history) - 1, -1, -1) if is_real_user(history[i])), -1)


def split_points(history: list[dict[str, Any]], sizes: list[int], retain_ratio: float) -> tuple[int, int]:
    """Return `(head_end, tail_start)`; the middle `history[head_end:tail_start]` is replaced.

    Head = through the first user message. Tail = last ≥ TAIL_MIN_MESSAGES messages holding
    ≥ retain_ratio of the tokens, extended back so it starts at a real user message (which
    also guarantees no tool_call/result pair is split)."""
    n = len(history)
    head_end = next((i + 1 for i, m in enumerate(history) if m.get("role") == "user"), 0)
    start = max(head_end, n - (TAIL_MIN_MESSAGES if retain_ratio > 0 else 0))
    if retain_ratio > 0:
        target = retain_ratio * sum(sizes)
        kept = sum(sizes[start:])
        while start > head_end and kept < target:
            start -= 1
            kept += sizes[start]
    while start > head_end and (start >= n or not is_real_user(history[start])):
        start -= 1
    return head_end, start


def prune_plan(history: list[dict[str, Any]]) -> tuple[dict[int, dict[str, Any]], int]:
    """S2: old bulky read-type tool results → head + marker + tail. Returns (changes, chars)."""
    names = {
        tc.get("id"): (tc.get("function") or {}).get("name")
        for m in history
        if m.get("role") == "assistant"
        for tc in m.get("tool_calls") or []
    }
    changes: dict[int, dict[str, Any]] = {}
    reclaimed = 0
    for i, m in enumerate(history[: max(0, len(history) - PRUNE_KEEP_RECENT)]):
        content = m.get("content")
        if m.get("role") != "tool" or not isinstance(content, str) or len(content) <= PRUNE_MIN_CHARS:
            continue
        if names.get(m.get("tool_call_id")) not in PRUNABLE_TOOLS:
            continue
        cut = len(content) - PRUNE_HEAD - PRUNE_TAIL
        new = f"{content[:PRUNE_HEAD]}\n… [pruned {cut} chars] …\n{content[-PRUNE_TAIL:]}"
        changes[i] = {**m, "content": new}
        reclaimed += len(content) - len(new)
    return changes, reclaimed


def _parts_text(content: Any) -> str:
    """Flatten list content for the summarizer; images become short placeholders, never base64."""
    if not content:
        return ""
    if not isinstance(content, list):
        return json.dumps(content)
    out: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            out.append(str(part))
        elif part.get("type") == "text":
            out.append(str(part.get("text", "")))
        elif part.get("type") == "image_url":
            out.append("[image]")
        else:
            out.append(json.dumps(part)[:200])
    return "\n".join(out)


def render_transcript(messages: list[dict[str, Any]], cap: int = SUMMARY_INPUT_CHARS) -> str:
    """Plain-text transcript for the summarizer; oldest entries are dropped past `cap`."""
    entries: list[str] = []
    for m in messages:
        content = m.get("content")
        text = content if isinstance(content, str) else _parts_text(content)
        if m.get("role") == "tool":
            entries.append(f"[tool result {m.get('tool_call_id', '')}]\n{text}")
            continue
        entry = f"[{m.get('role')}]\n{text}".rstrip()
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            entry += f"\n-> {fn.get('name')}({str(fn.get('arguments', ''))[:2000]})"
        entries.append(entry)
    total = sum(len(e) + 2 for e in entries)
    first = 0
    while first < len(entries) - 1 and total > cap:
        total -= len(entries[first]) + 2
        first += 1
    kept = entries[first:]
    if kept and len(kept[0]) > cap:
        kept[0] = kept[0][-cap:]
    note = [f"[... {first} older messages omitted ...]"] if first else []
    return "\n\n".join(note + kept)


class CompactionService:
    """Exposed as `AgentRunner.compaction`; the manual API calls `compact(...)` directly."""

    def __init__(self, services: Services, ledger: UsageLedger | None = None) -> None:
        self.s = services
        self.ledger = ledger
        self.failures: dict[str, int] = {}

    # ---------------------------------------------------------------- meter
    @property
    def enabled(self) -> bool:
        return self.s.settings.loop.compaction_enabled

    def tripped(self, session_id: str) -> bool:
        return self.failures.get(session_id, 0) >= MAX_FAILURES

    def thresholds(self, model: str, max_output: int | None = None) -> Thresholds:
        loop = self.s.settings.loop
        ratio = (
            loop.compaction_threshold_ratio if "compaction_threshold_ratio" in loop.model_fields_set else None
        )
        return thresholds(self.s.gateway.context_window(model), max_output, ratio)

    def tokens(self, model: str, messages: Any) -> int:
        return self.s.gateway.count_tokens(model, json.dumps(list(messages), default=str))

    async def pre_step(self, rt: SessionRuntime, request: LLMRequest) -> bool:
        """S4(a). True when history changed and the caller must rebuild the request.
        Raises CompactionError only when this failure trips the circuit breaker."""
        if not self.enabled or self.tripped(rt.session_id):
            return False
        th = self.thresholds(request.model, request.max_tokens)
        pressure = self.tokens(request.model, request.messages)
        if pressure < th.auto:
            return False
        overhead = max(0, pressure - self.tokens(request.model, rt.history))
        try:
            await self.compact(
                rt.session_id, trigger="pressure", rt=rt, model=request.model, stop_below=th.auto - overhead
            )
        except CompactionError as exc:
            if exc.tripped:
                raise
            return False
        return True

    async def recover_overflow(self, rt: SessionRuntime, exc: GatewayError, model: str) -> None:
        """S4(b): on a provider CONTEXT error compact with retain=0 so the caller can retry
        once; anything else (or a failed compaction) re-raises the original error."""
        if exc.code != "CONTEXT" or not self.enabled or self.tripped(rt.session_id):
            raise exc
        try:
            await self.compact(rt.session_id, trigger="overflow", retain_ratio=0, rt=rt, model=model)
        except CompactionError as cexc:
            if cexc.tripped:
                raise
            raise exc from cexc

    # -------------------------------------------------------------- compact
    async def compact(
        self,
        session_id: str,
        *,
        trigger: Trigger,
        retain_ratio: float | None = None,
        rt: SessionRuntime | None = None,
        model: str | None = None,
        stop_below: int | None = None,
    ) -> CompactionResult:
        rt = rt or await self.s.coordinator.runtime(session_id)
        model = model or self._model(rt)
        retain = self.s.settings.loop.compaction_retain_ratio if retain_ratio is None else retain_ratio
        cid = f"cmp_{uuid.uuid4().hex[:12]}"
        await self.s.emit(session_id, "compaction.started", {"trigger": trigger, "compaction_id": cid})
        obs.count("compaction_runs_total")  # --- W0-D
        await self._hook("PreCompact", rt, {"trigger": trigger, "compaction_id": cid})
        try:
            res = await self._compact(rt, cid, trigger, retain, model, stop_below)
        except NothingToCompact as exc:
            await self.s.emit(
                session_id,
                "compaction.skipped",
                {"trigger": trigger, "compaction_id": cid, "reason": exc.reason},
            )
            raise
        except Exception as exc:
            n = self.failures[session_id] = self.failures.get(session_id, 0) + 1
            reason = exc.reason if isinstance(exc, CompactionError) else f"{type(exc).__name__}: {exc}"
            if not isinstance(exc, CompactionError):
                log.exception("compaction crashed")
            await self.s.emit(
                session_id,
                "compaction.failed",
                {"trigger": trigger, "compaction_id": cid, "reason": reason, "failures": n},
            )
            raise CompactionError(reason, tripped=n >= MAX_FAILURES) from exc
        self.failures[session_id] = 0
        await self.s.emit(
            session_id,
            "compaction.completed",
            {
                "trigger": trigger,
                "compaction_id": cid,
                "pre_tokens": res.pre_tokens,
                "post_tokens": res.post_tokens,
                "archived": res.archived,
                "pruned": res.pruned,
                "summary_chars": res.summary_chars,
            },
        )
        await self._hook(
            "PostCompact",
            rt,
            {
                "trigger": trigger,
                "compaction_id": cid,
                "pre_tokens": res.pre_tokens,
                "post_tokens": res.post_tokens,
            },
        )
        return res

    async def _hook(self, event: HookEvent, rt: SessionRuntime, payload: dict[str, Any]) -> None:
        """Pre/PostCompact hooks are informational; a broken hook never fails compaction."""
        hooks = hooks_of(self.s)
        if hooks is None:
            return
        try:
            payload = {"agent": rt.agent, **payload}
            await hooks.run(event, session_id=rt.session_id, payload=payload, match_key=rt.agent)
        except Exception:
            log.exception("%s hooks failed", event)

    def _model(self, rt: SessionRuntime) -> str:
        from auoreo.gateway.resolve import resolve_model

        agent_model = None
        if rt.agent in self.s.harness.agents:
            agent_model = self.s.harness.agent(rt.agent).manifest.model
        return resolve_model(self.s.settings, session_override=rt.model, agent_model=agent_model)

    async def _compact(
        self,
        rt: SessionRuntime,
        cid: str,
        trigger: Trigger,
        retain: float,
        model: str,
        stop_below: int | None,
    ) -> CompactionResult:
        history = rt.history
        pre = self.tokens(model, history)
        pruned = await self._prune(rt)
        if pruned and stop_below is not None and (now := self.tokens(model, history)) < stop_below:
            return CompactionResult(pre, now, 0, cid, 0, pruned)
        sizes = [self.tokens(model, [m]) for m in history]
        head_end, tail_start = split_points(history, sizes, retain)
        if tail_start <= head_end:
            raise NothingToCompact(f"nothing to compact ({len(history)} messages, all protected)")
        middle = history[head_end:tail_start]
        summary = await self._summarize(rt.session_id, cid, model, middle)
        msg = {
            "role": "user",
            "content": f'{SUMMARY_TAG} trigger="{trigger}" id="{cid}" archived="{len(middle)}">\n'
            f"{summary}\n</compaction-summary>",
        }
        new = [*history[:head_end], msg, *history[tail_start:]]
        post = self.tokens(model, new)
        if post >= pre:  # shrink proof: never replace with something bigger
            raise CompactionError(f"summary did not shrink the context ({pre} -> {post} tokens)")
        await self._replace(rt, cid, head_end, tail_start, msg)
        history[:] = new  # same list object: the runner and coordinator keep their reference
        if self.s.memory is not None:
            try:
                await self.s.memory.record_message(rt.session_id, "compaction", summary)
            except Exception:  # indexing must never undo a committed compaction
                log.exception("failed to index compaction summary")
        return CompactionResult(pre, post, len(middle), cid, len(summary), pruned)

    async def _rows(self, s: Any, rt: SessionRuntime) -> list[MessageRow]:
        q = select(MessageRow).where(MessageRow.session_id == rt.session_id).order_by(MessageRow.idx)
        rows = list((await s.execute(q)).scalars())
        if len(rows) != len(rt.history):
            raise CompactionError(f"history out of sync ({len(rows)} rows vs {len(rt.history)} in memory)")
        return rows

    async def _prune(self, rt: SessionRuntime) -> int:
        changes, reclaimed = prune_plan(rt.history)
        if not changes or reclaimed // 4 < PRUNE_MIN_RECLAIM_TOKENS:
            return 0  # hysteresis: tiny reclaims are not worth a cache miss
        async with self.s.db.session() as s:
            rows = await self._rows(s, rt)
            for i, m in changes.items():
                rows[i].message = m
            await s.commit()
        for i, m in changes.items():
            rt.history[i] = m
        return len(changes)

    async def _replace(
        self, rt: SessionRuntime, cid: str, head_end: int, tail_start: int, msg: dict[str, Any]
    ) -> None:
        async with self.s.db.session() as s:
            rows = await self._rows(s, rt)
            for r in rows[head_end:tail_start]:
                s.add(
                    MessageArchiveRow(
                        session_id=rt.session_id, compaction_id=cid, idx=r.idx, message=r.message
                    )
                )
            kept = [(dict(r.message), r.created_at) for r in rows[tail_start:]]
            for r in rows[head_end:]:
                await s.delete(r)
            await s.flush()  # deletes before inserts: (session_id, idx) is unique
            base = rows[head_end - 1].idx + 1 if head_end else 0
            s.add(MessageRow(session_id=rt.session_id, idx=base, message=msg))
            for j, (m, created) in enumerate(kept, start=1):
                s.add(MessageRow(session_id=rt.session_id, idx=base + j, message=m, created_at=created))
            await s.commit()

    async def _summarize(self, sid: str, cid: str, model: str, middle: list[dict[str, Any]]) -> str:
        prompt = self.s.harness.snapshot.prompts.get("compaction")
        if prompt is None or prompt.section.role != "compaction":
            raise CompactionError("prompts/compaction.md (role: compaction) is missing")
        group = "small" if "small" in self.s.settings.models else model
        request = LLMRequest(
            model=group,
            messages=(
                {"role": "system", "content": prompt.template},
                {"role": "user", "content": f"<transcript>\n{render_transcript(middle)}\n</transcript>"},
                {"role": "user", "content": WRITE_NOW},
            ),
            tool_choice="none",
            temperature=0,
            max_tokens=SUMMARY_MAX_TOKENS,
            metadata={"session_id": sid, "purpose": "compaction", "compaction_id": cid},
        )
        parts: list[str] = []
        record: Callable[..., Any] | None = self.ledger.record if self.ledger else None
        try:
            async for item in self.s.gateway.stream(request):  # no compaction can recurse from here
                if isinstance(item, TextDelta):
                    parts.append(item.text)
                elif isinstance(item, Usage) and record is not None:
                    await record(sid, cid, group, item)
        except GatewayError as exc:
            raise CompactionError(f"summarizer call failed: {exc.code}: {exc.message[:300]}") from exc
        text = "".join(parts).strip()
        if not text:
            raise CompactionError("summarizer returned no text")
        return text


__all__ = [
    "CompactionError",
    "CompactionResult",
    "CompactionService",
    "Thresholds",
    "prune_plan",
    "render_transcript",
    "split_points",
    "turn_index",
    "thresholds",
]
