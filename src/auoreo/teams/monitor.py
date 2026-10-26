"""Watching one running child turn (spec §16.1, §25.11): live log, heartbeat, staleness, budget.

While a delegated turn runs, `ChildMonitor` polls the child's events every
`teams2.delegation.poll_s` and

- appends them to `<home>/sessions/<parent>/delegation/live/<sa-id>/task-<n>.log` (JSON
  lines, no deltas);
- every `heartbeat_s` emits `subagent.progress` on the parent (idle seconds, usage so far)
  and bumps `TeamsService.last_activity[parent]`;
- tracks whether a tool call is open (`tool.started` without `tool.completed`): without a
  child event for `i3.delegation.stale_idle_s` (450 s) outside a tool call, or
  `stale_in_tool_s` (1200 s) inside one, it stops the child and records `STALE`; one
  `subagent.progress` with `stale: "warn"` comes first (half the limit or `stale_warn_s`);
- enforces `budget{max_tokens,max_usd}` (child tree usage): the first overrun injects one
  summarise-now grace note; another model call after it stops the child. Either way the
  result carries `TOOL_BUDGET_EXCEEDED`.

The monitor never raises into the turn: its verdict is read by `TeamsService._turn`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from auoreo.teams.service import Member, TeamsService

log = logging.getLogger(__name__)
BUDGET_EXCEEDED = "TOOL_BUDGET_EXCEEDED"
STALE = "STALE"
SKIP_TYPES = frozenset({"message.delta", "reasoning.delta", "tool.input_delta"})
LINE_CAP = 8_000
GRACE_NOTE = (  # `inject` adds the OUT_OF_BAND marker
    "Your delegation budget ({why}) is exhausted. This is your last reply: do not call tools. "
    "Summarize what you did, what is left, and answer now."
)


@dataclass(frozen=True)
class Budget:
    max_tokens: int | None = None
    max_usd: float | None = None
    timeout_ms: int | None = None

    @classmethod
    def of(cls, raw: dict[str, Any] | None) -> Budget:
        raw = raw or {}
        tokens, usd, ms = raw.get("max_tokens"), raw.get("max_usd"), raw.get("timeout_ms")
        return cls(
            int(tokens) if tokens is not None else None,
            float(usd) if usd is not None else None,
            int(ms) if ms is not None else None,
        )

    def exceeded(self, usage: dict[str, Any]) -> str | None:
        tokens = int(usage.get("prompt_tokens", 0)) + int(usage.get("completion_tokens", 0))
        if self.max_tokens is not None and tokens >= self.max_tokens:
            return f"max_tokens={self.max_tokens}, used {tokens}"
        if self.max_usd is not None and float(usage.get("cost_usd", 0.0)) >= self.max_usd:
            return f"max_usd={self.max_usd}, spent {float(usage.get('cost_usd', 0.0)):.4f}"
        return None


def live_log_path(home: Path, parent: str, subagent_id: str, n: int = 1) -> Path:
    """`<home>/sessions/<parent>/delegation/live/<sa-id>/task-<n>.log` (spec §16.1); `n` counts
    the child's turns (1 = the spawn, then one per resume)."""
    return Path(home) / "sessions" / parent / "delegation" / "live" / subagent_id / f"task-{n}.log"


class ChildMonitor:
    def __init__(self, teams: TeamsService, m: Member, budget: Budget) -> None:
        self.t = teams
        self.m = m
        self.budget = budget
        cfg = teams.s.settings.teams2.delegation
        self.cfg = cfg
        sid = m.subagent_id or m.session_id
        self.path = live_log_path(teams.s.settings.home, m.parent_session_id, sid, max(m.runs, 1))
        self.i3 = teams.s.settings.i3.delegation
        self.open_tools: set[str] = set()  # call ids between tool.started and tool.completed
        self.verdict: dict[str, Any] | None = None
        self.budget_hit: str | None = None
        self._grace_calls: int | None = None
        self._seq = 0
        self._last_event = time.monotonic()
        self._last_beat = time.monotonic()
        self._warned = False
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> ChildMonitor:
        self._seq = await self.t.s.events.last_seq()
        self._last_event = self._last_beat = time.monotonic()
        self._task = asyncio.create_task(self._loop(), name=f"subagent-monitor:{self.m.session_id}")
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        with contextlib.suppress(Exception):  # flush what the turn wrote after the last poll
            await self._drain()

    # ------------------------------------------------------------ loop
    async def _loop(self) -> None:
        while self.verdict is None:
            await asyncio.sleep(self.cfg.poll_s)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # monitoring must never kill the child
                log.exception("subagent monitor tick failed for %s", self.m.session_id)

    async def tick(self) -> None:
        now = time.monotonic()
        if await self._drain():
            self._last_event, self._warned = now, False
        idle = now - self._last_event
        usage = await self._usage() if self.budget.max_tokens or self.budget.max_usd else None
        if now - self._last_beat >= self.cfg.heartbeat_s:
            self._last_beat = now
            await self._progress({"idle_s": round(idle, 1), **({"usage": usage} if usage else {})})
        limit, where = self.stale_limit()
        if idle >= limit:
            msg = f"no activity for {idle:.0f}s {where} (limit {limit:.0f}s)"
            await self._stop({"code": STALE, "message": msg})
            return
        if idle >= min(self.cfg.stale_warn_s, limit / 2) and not self._warned:
            self._warned = True
            await self._progress({"idle_s": round(idle, 1), "stale": "warn"})
        if usage is not None:
            await self._check_budget(usage)

    def stale_limit(self) -> tuple[float, str]:
        """Idle limit: `stale_idle_s` (450 s) outside a tool call, `stale_in_tool_s` (1200 s)
        while one runs; `teams2.delegation.stale_fail_s` caps both."""
        if self.open_tools:
            return min(self.i3.stale_in_tool_s, self.cfg.stale_fail_s), "inside a tool call"
        return min(self.i3.stale_idle_s, self.cfg.stale_fail_s), "outside tool calls"

    def _track(self, etype: str, data: dict[str, Any]) -> None:
        call = str(data.get("call_id") or "")
        if etype == "tool.started":
            self.open_tools.add(call)
        elif etype == "tool.completed":
            self.open_tools.discard(call)
        elif etype.startswith("run."):  # a turn boundary closes whatever was left open
            self.open_tools.clear()

    async def _check_budget(self, usage: dict[str, Any]) -> None:
        why = self.budget.exceeded(usage)
        if why is None:
            return
        calls = int(usage.get("calls", 0))
        if self._grace_calls is None:
            self.budget_hit, self._grace_calls = why, calls
            await self._progress({"budget": "grace", "reason": why, "usage": usage})
            await self.t.inject(self.m.session_id, GRACE_NOTE.format(why=why))
        elif calls > self._grace_calls + 1:  # the grace reply came and it still kept going
            await self._stop({"code": BUDGET_EXCEEDED, "message": f"delegation budget exceeded ({why})"})

    async def _stop(self, error: dict[str, Any]) -> None:
        self.verdict = error
        await self._progress({"stopped": error["code"], "reason": error["message"]})
        await self.t.s.coordinator.cancel(self.m.session_id)

    # ------------------------------------------------------------ helpers
    async def _usage(self) -> dict[str, Any]:
        ledger = self.t.s.coordinator.runner.usage
        return await ledger.totals(await ledger.session_tree(self.m.session_id))

    async def _progress(self, extra: dict[str, Any]) -> None:
        self.t.last_activity[self.m.parent_session_id] = time.time()
        data = {
            "parent": self.m.parent_session_id,
            "child": self.m.session_id,
            "name": self.m.name,
            "agent": self.m.agent,
            **extra,
        }
        await self.t.s.emit(self.m.parent_session_id, "subagent.progress", data)
        self._write([{"ts": time.time(), "type": "subagent.progress", "data": extra}])

    async def _drain(self) -> int:
        """Read the child's new events; returns how many there were."""
        events = await self.t.s.events.replay(self._seq, frozenset({f"session:{self.m.session_id}"}), 500)
        if not events:
            return 0
        self._seq = events[-1].seq
        for e in events:
            self._track(e.type, e.data if isinstance(e.data, dict) else {})
        lines = [
            {"ts": e.created_at.timestamp(), "seq": e.seq, "type": e.type, "data": e.data}
            for e in events
            if e.type not in SKIP_TYPES
        ]
        self._write(lines)
        return len(events)

    def _write(self, lines: list[dict[str, Any]]) -> None:
        if not lines or not self.cfg.live_logs:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                for line in lines:
                    text = json.dumps(line, default=str, ensure_ascii=False)
                    if len(text) > LINE_CAP:
                        short = {k: v for k, v in line.items() if k != "data"}
                        text = json.dumps({**short, "truncated": True}, default=str)
                    fh.write(text + "\n")
        except OSError as exc:
            log.warning("cannot write delegation live log %s: %s", self.path, exc)
