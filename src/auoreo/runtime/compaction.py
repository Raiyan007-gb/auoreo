"""Context compaction (spec §11.9, cheapest-first subset S0, S2, S4, S5, S6, S7).

S0 meter     — thresholds from the model's context window; pressure = token count of the
               assembled request.
S2 prune     — shorten old, bulky shell/read/web/edit tool results in place (no LLM).
S5 summarize — one tool-free aux call (`runtime.compaction_summarize`): KV-friendly prefix
               replay, PTL retries, timeout ladder, `finish_reason=length` aborts.
S6 replace   — keep the head (through the first user message) and a tail that starts at a
               real user message; swap the middle for one `<compaction-summary>` user message
               and move the replaced rows to `messages_archive`.
S7 restore   — session-memory compaction first (no LLM) when notes exist; the summary
               message carries restored files/skills/plan/metadata; SessionStart re-runs.
Triggers (S4): pre-step pressure + provider CONTEXT overflow (runner), idle
(`runtime.compaction_idle`), manual `/compact` + API with optional `up_to` / `from` bounds.
Pure helpers live in `runtime.compaction_plan` and are re-exported here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import select

from auoreo.core import metrics_text
from auoreo.core import observability as obs
from auoreo.core.db import MessageArchiveRow, MessageRow
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.types import LLMRequest
from auoreo.harness.manifests import HookEvent
from auoreo.memory.recall import memory_for_agent_name
from auoreo.runtime.compaction_extras import assemble
from auoreo.runtime.compaction_i4 import PostCompaction, hygiene_due, meter, skill_prune_plan
from auoreo.runtime.compaction_plan import (
    PRUNABLE_TOOLS,
    PRUNE_HEAD,
    PRUNE_MIN_RECLAIM_TOKENS,
    PRUNE_TAIL,
    SUMMARY_TAG,
    Thresholds,
    partial_bounds,
    prune_plan,
    render_transcript,
    session_memory_split,
    split_points,
    thresholds,
    turn_index,
)
from auoreo.runtime.compaction_restore import read_session_memory, rerun_session_start, restore_block
from auoreo.runtime.compaction_summarize import Summarizer, SummaryError
from auoreo.runtime.hooks import hooks_of

if TYPE_CHECKING:
    from auoreo.core.services import Services
    from auoreo.core.usage import UsageLedger
    from auoreo.runtime.runner import SessionRuntime

log = logging.getLogger(__name__)
# Registered here (the `gateway/budgets.py` precedent): `observability.count` drops names
# missing from its allowlist, which silently lost these three before M7.
_METRICS = {
    "compaction_completed_total": (
        {"trigger", "source"},
        "Context compactions completed, by trigger, source.",
    ),
    "compaction_failures_total": ({"trigger", "failure_class"}, "Context compactions that failed."),
    "compaction_tokens_reclaimed_total": (set(), "Prompt tokens reclaimed by compaction."),
}
for _name, (_labels, _help) in _METRICS.items():
    obs.METRICS.setdefault(_name, ("counter", frozenset(_labels)))
    metrics_text.HELP.setdefault(_name, _help)

Trigger = Literal["pressure", "overflow", "manual", "idle", "hygiene"]
MAX_FAILURES = 3
DOCTOR_HINT = "compaction stopped after 3 consecutive failures; run `auoreo doctor`"


class CompactionError(Exception):
    def __init__(self, reason: str, *, tripped: bool = False, failure_class: str = "error") -> None:
        super().__init__(reason)
        self.reason = reason
        self.tripped = tripped
        self.failure_class = failure_class


class NothingToCompact(CompactionError):
    """Benign: every message is protected (head/tail). Not a failure, never trips the breaker."""


@dataclass(frozen=True)
class CompactionResult:
    pre_tokens: int
    post_tokens: int
    archived: int
    compaction_id: str
    summary_chars: int
    pruned: int = 0
    source: str = "summary"  # summary | session_memory | prune
    retries: int = 0
    summary_model: str | None = None
    cache_read: int = 0
    cache_creation: int = 0


class CompactionService:
    """Exposed as `AgentRunner.compaction`; the manual API calls `compact(...)` directly."""

    def __init__(self, services: Services, ledger: UsageLedger | None = None) -> None:
        self.s = services
        self.ledger = ledger
        self.summarizer = Summarizer(services, ledger)
        self.failures: dict[str, int] = {}
        self.committing: set[str] = set()
        """Sessions inside the replace transaction: a foreground claim waits, never cancels."""
        self.active: set[str] = set()  # recursion guard: never compact inside a compaction
        self.post = PostCompaction()  # notifyCompaction() + markPostCompaction()
        self._warned: set[str] = set()

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
        window = self.s.gateway.context_window(model)
        return meter(self.s.settings.i4.compaction, window, model, max_output, ratio)

    def guarded(self, rt: SessionRuntime) -> bool:
        """Recursion guard (S4): no auto-compaction inside a compaction or a subagent."""
        return rt.subagent or rt.session_id in self.active

    def tokens(self, model: str, messages: Any) -> int:
        return self.s.gateway.count_tokens(model, json.dumps(list(messages), default=str))

    async def pre_step(self, rt: SessionRuntime, request: LLMRequest) -> bool:
        """S4(a). True when history changed and the caller must rebuild the request.
        Raises CompactionError only when this failure trips the circuit breaker."""
        if not self.enabled or self.tripped(rt.session_id) or self.guarded(rt):
            return False
        th = self.thresholds(request.model, request.max_tokens)
        pressure = self.tokens(request.model, request.messages)
        await self._warn(rt.session_id, pressure, th)
        hygiene = hygiene_due(self.s.settings.i4.compaction, th.window, pressure)
        if pressure < th.auto and not hygiene:
            return False
        trigger: Trigger = "pressure" if pressure >= th.auto else "hygiene"
        overhead = max(0, pressure - self.tokens(request.model, rt.history))
        try:
            await self.compact(
                rt.session_id, trigger=trigger, rt=rt, model=request.model, stop_below=th.auto - overhead
            )
        except CompactionError as exc:
            if exc.tripped:
                raise
            return False
        return True

    async def recover_overflow(self, rt: SessionRuntime, exc: GatewayError, model: str) -> None:
        """S4(b): on a provider CONTEXT error compact with retain=0 so the caller can retry
        once; anything else (or a failed compaction) re-raises the original error."""
        if exc.code != "CONTEXT" or not self.enabled or self.tripped(rt.session_id) or self.guarded(rt):
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
        up_to: int | None = None,
        from_: int | None = None,
    ) -> CompactionResult:
        rt = rt or await self.s.coordinator.runtime(session_id)
        if session_id in self.active:
            raise NothingToCompact("a compaction is already running for this session")
        self.active.add(session_id)
        try:
            return await self._run(session_id, trigger, retain_ratio, rt, model, stop_below, up_to, from_)
        finally:
            self.active.discard(session_id)

    async def _warn(self, sid: str, pressure: int, th: Thresholds) -> None:
        if pressure < th.warn or sid in self._warned:
            return
        self._warned.add(sid)
        data = {"pressure": pressure, "warn": th.warn, "auto": th.auto, "window": th.window}
        await self.s.emit(sid, "compaction.warning", data)

    async def _run(
        self,
        session_id: str,
        trigger: Trigger,
        retain_ratio: float | None,
        rt: SessionRuntime,
        model: str | None,
        stop_below: int | None,
        up_to: int | None,
        from_: int | None,
    ) -> CompactionResult:
        model = model or self._model(rt)
        retain = self.s.settings.loop.compaction_retain_ratio if retain_ratio is None else retain_ratio
        cid = f"cmp_{uuid.uuid4().hex[:12]}"
        base = {"trigger": trigger, "compaction_id": cid}
        await self.s.emit(session_id, "compaction.started", base)
        obs.count("compaction_runs_total")  # --- W0-D
        await self._hook("PreCompact", rt, base)
        bounds = (up_to, from_) if up_to is not None or from_ is not None else None
        try:
            res = await self._compact(rt, cid, trigger, retain, model, stop_below, bounds)
        except NothingToCompact as exc:
            await self.s.emit(session_id, "compaction.skipped", {**base, "reason": exc.reason})
            raise
        except asyncio.CancelledError:  # a foreground claim pre-empted a background compaction
            await self.s.emit(session_id, "compaction.failed", {**base, "reason": "cancelled", "failures": 0})
            raise
        except Exception as exc:
            n = self.failures[session_id] = self.failures.get(session_id, 0) + 1
            reason = exc.reason if isinstance(exc, CompactionError) else f"{type(exc).__name__}: {exc}"
            cls = exc.failure_class if isinstance(exc, CompactionError) else "crash"
            if not isinstance(exc, CompactionError):
                log.exception("compaction crashed")
            if n >= MAX_FAILURES:  # circuit breaker: stop + doctor hint
                reason = f"{reason} — {DOCTOR_HINT}"
            data = {**base, "reason": reason, "failures": n, "failure_class": cls}
            await self.s.emit(session_id, "compaction.failed", data)
            obs.count("compaction_failures_total", trigger=trigger, failure_class=cls)
            raise CompactionError(reason, tripped=n >= MAX_FAILURES, failure_class=cls) from exc
        self.failures[session_id] = 0
        await self._completed(rt, base, res, model)
        if res.archived:
            await rerun_session_start(self.s, rt, cid)
        await self._hook(
            "PostCompact", rt, {**base, "pre_tokens": res.pre_tokens, "post_tokens": res.post_tokens}
        )
        return res

    async def _completed(
        self, rt: SessionRuntime, base: dict[str, Any], res: CompactionResult, model: str
    ) -> None:
        """`compaction.completed` (spec §11.9 `oreo_compact` fields) + metrics + S7 notify/mark."""
        th = self.thresholds(model)
        data = {
            **base,
            "pre_tokens": res.pre_tokens,
            "post_tokens": res.post_tokens,
            "threshold": th.auto,
            "window": th.window,
            "archived": res.archived,
            "pruned": res.pruned,
            "summary_chars": res.summary_chars,
            "source": res.source,
            "retries": res.retries,
            "summary_model": res.summary_model,
            "cache_read": res.cache_read,
            "cache_creation": res.cache_creation,
            "failure_class": None,
        }
        await self.s.emit(rt.session_id, "compaction.completed", data)
        obs.count("compaction_completed_total", trigger=base["trigger"], source=res.source)
        obs.count("compaction_tokens_reclaimed_total", max(0, res.pre_tokens - res.post_tokens))
        self._warned.discard(rt.session_id)
        if res.archived or res.pruned:
            self.post.mark_post_compaction(rt.session_id, base["compaction_id"])
            await self.post.notify_compaction(rt.session_id, data)

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
        bounds: tuple[int | None, int | None] | None,
    ) -> CompactionResult:
        history = rt.history
        pre = self.tokens(model, history)
        pruned = await self._prune(rt)
        if pruned and stop_below is not None and (now := self.tokens(model, history)) < stop_below:
            return CompactionResult(pre, now, 0, cid, 0, pruned, "prune")
        sizes = [self.tokens(model, [m]) for m in history]
        default = split_points(history, sizes, retain)
        if bounds is None and (sm := await self._session_memory(rt, cid, trigger, model, sizes, pre)):
            start, end, msg, chars = sm
            meta = CompactionResult(0, 0, 0, cid, chars, pruned, "session_memory")
            return await self._commit(rt, cid, start, end, msg, pre, model, meta)
        start, end = default if bounds is None else partial_bounds(history, default[1], *bounds)
        if end <= start:
            raise NothingToCompact(f"nothing to compact ({len(history)} messages, all protected)")
        try:
            out = await self.summarizer.summarize(rt, cid, model, history[:start], history[start:end])
        except SummaryError as exc:
            raise CompactionError(exc.reason, failure_class=exc.failure_class) from exc
        checkpoint = await self._message(rt, cid, trigger, out.text, start, end, pre, model)
        if checkpoint is None:  # shrink proof: never replace with something bigger
            raise CompactionError("summary did not shrink the context", failure_class="no_shrink")
        meta = CompactionResult(
            0, 0, 0, cid, len(out.text), pruned, "summary", out.retries, out.model, out.cache_read
        )
        return await self._commit(rt, cid, start, end, checkpoint, pre, model, meta)

    async def _session_memory(
        self, rt: SessionRuntime, cid: str, trigger: Trigger, model: str, sizes: list[int], pre: int
    ) -> tuple[int, int, dict[str, Any], int] | None:
        """S7: LLM-free compaction from the session notes file when it exists and shrinks."""
        cfg = self.s.settings.compaction
        notes = read_session_memory(self.s.settings.home, rt.session_id) if cfg.session_memory else None
        if notes is None:
            return None
        start, end = session_memory_split(
            rt.history,
            sizes,
            cfg.session_memory_min_tokens,
            cfg.session_memory_min_messages,
            cfg.session_memory_max_tokens,
        )
        if end <= start:
            return None
        msg = await self._message(rt, cid, trigger, notes, start, end, pre, model, source="session_memory")
        return (start, end, msg, len(notes)) if msg is not None else None

    async def _message(
        self,
        rt: SessionRuntime,
        cid: str,
        trigger: Trigger,
        summary: str,
        start: int,
        end: int,
        pre: int,
        model: str,
        source: str = "summary",
    ) -> dict[str, Any] | None:
        """The checkpoint message (+ S7 restore block when it still fits) or None when even
        the bare summary would not shrink the context."""
        history = rt.history
        head = f'{SUMMARY_TAG} trigger="{trigger}" id="{cid}" archived="{end - start}" source="{source}">'
        middle = history[start:end]
        full, lean = (
            f"{head}\n{assemble(summary, middle, rt.session_id, start, full=f)}\n</compaction-summary>"
            for f in (True, False)
        )
        restored = await restore_block(self.s, rt, middle, history[end:])
        candidates = [f"{full}\n{restored}", f"{lean}\n{restored}"] if restored else []
        candidates += [full, lean, f"{head}\n{summary}\n</compaction-summary>"]
        for content in candidates:
            msg = {"role": "user", "content": content}
            if self.tokens(model, [*history[:start], msg, *history[end:]]) < pre:
                return msg
        return None

    async def _commit(
        self,
        rt: SessionRuntime,
        cid: str,
        start: int,
        end: int,
        msg: dict[str, Any],
        pre: int,
        model: str,
        meta: CompactionResult,
    ) -> CompactionResult:
        history = rt.history
        new = [*history[:start], msg, *history[end:]]
        post = self.tokens(model, new)
        self.committing.add(rt.session_id)
        try:
            await self._replace(rt, cid, start, end, msg)
            history[:] = new  # same list object: the runner and coordinator keep their reference
        finally:
            self.committing.discard(rt.session_id)
        if self.s.memory is not None:
            try:
                memory = await memory_for_agent_name(self.s, rt.agent)  # --- M7: the agent's tenant
                if memory is not None:
                    await memory.record_message(rt.session_id, "compaction", str(msg["content"]))
            except Exception:  # indexing must never undo a committed compaction
                log.exception("failed to index compaction summary")
        return replace(meta, pre_tokens=pre, post_tokens=post, archived=end - start)

    async def _rows(self, s: Any, rt: SessionRuntime) -> list[MessageRow]:
        q = select(MessageRow).where(MessageRow.session_id == rt.session_id).order_by(MessageRow.idx)
        rows = list((await s.execute(q)).scalars())
        if len(rows) != len(rt.history):
            raise CompactionError(f"history out of sync ({len(rows)} rows vs {len(rt.history)} in memory)")
        return rows

    async def _prune(self, rt: SessionRuntime) -> int:
        changes, reclaimed = prune_plan(rt.history)
        cfg = self.s.settings.i4.compaction
        skills, skill_reclaimed = skill_prune_plan(rt.history, cfg.skill_prune_min_chars, cfg.skill_prune_cap)
        changes, reclaimed = {**changes, **skills}, reclaimed + skill_reclaimed
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

    async def _replace(self, rt: SessionRuntime, cid: str, start: int, end: int, msg: dict[str, Any]) -> None:
        async with self.s.db.session() as s:
            rows = await self._rows(s, rt)
            for r in rows[start:end]:
                s.add(
                    MessageArchiveRow(
                        session_id=rt.session_id, compaction_id=cid, idx=r.idx, message=r.message
                    )
                )
            kept = [(dict(r.message), r.created_at) for r in rows[end:]]
            for r in rows[start:]:
                await s.delete(r)
            await s.flush()  # deletes before inserts: (session_id, idx) is unique
            base = rows[start - 1].idx + 1 if start else 0
            s.add(MessageRow(session_id=rt.session_id, idx=base, message=msg))
            for j, (m, created) in enumerate(kept, start=1):
                s.add(MessageRow(session_id=rt.session_id, idx=base + j, message=m, created_at=created))
            await s.commit()


__all__ = [
    "PRUNABLE_TOOLS",
    "PRUNE_HEAD",
    "PRUNE_TAIL",
    "CompactionError",
    "CompactionResult",
    "CompactionService",
    "NothingToCompact",
    "Thresholds",
    "prune_plan",
    "render_transcript",
    "split_points",
    "thresholds",
    "turn_index",
]
