"""H4: machinery for `auoreo loop` and `auoreo heartbeat` (spec §18).

Both repeat one prompt on an interval in ONE in-process `Services` (one DB pool, one MCP
bridge) and the same session unless `fresh`, until Ctrl-C or the iteration limit. Each turn
goes through the normal coordinator path (`run_support._one_turn`), so permissions, audit,
compaction and titles behave exactly like `auoreo run` (non-interactive: asks are denied).
"""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auoreo.cli.run_support import RunPlan, _one_turn, resolve_session
from auoreo.core.services import Services
from auoreo.core.settings import Settings

_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_PART = re.compile(r"(\d+(?:\.\d+)?)([smhd])")


def parse_interval(text: str) -> float:
    """`90` / `30s` / `5m` / `1h30m` / `1d` → seconds."""
    raw = text.strip().lower().replace(" ", "")
    if re.fullmatch(r"\d+(?:\.\d+)?", raw):
        return float(raw)
    if not raw or _PART.sub("", raw):
        raise ValueError(f"bad interval '{text}' (use e.g. 30s, 5m, 1h30m)")
    return sum(float(n) * _UNIT[u] for n, u in _PART.findall(raw))


@dataclass
class Beat:
    index: int
    session_id: str
    status: str
    text: str


async def repeat(
    st: Settings,
    plan: RunPlan,
    *,
    interval_s: float,
    limit: int,
    fresh: bool = False,
    prompt_for: Callable[[int], str] | None = None,
    on_beat: Callable[[Beat], None] | None = None,
    pretty: Any = None,
) -> list[Beat]:
    """Run `plan` up to `limit` times, `interval_s` apart (measured from turn end)."""
    beats: list[Beat] = []
    svc = await Services.create(st, cwd=Path.cwd(), with_mcp=True)
    try:
        sid = await resolve_session(svc, plan)
        for i in range(limit):
            if i and fresh:
                sid = await resolve_session(svc, replace(plan, session=None, cont=False, fork=None))
            text = prompt_for(i) if prompt_for else plan.prompt
            status, run_id = await _one_turn(svc, sid, text, plan, pretty)
            result = svc.coordinator.result(run_id) if run_id else None
            beat = Beat(i + 1, sid, status, result.text if result else "")
            beats.append(beat)
            if on_beat:
                on_beat(beat)
            if i + 1 < limit:
                await asyncio.sleep(interval_s)
    finally:
        await svc.close()
    return beats


def run_repeat(st: Settings, plan: RunPlan, **kw: Any) -> tuple[list[Beat], bool]:
    """`repeat` with Ctrl-C as a clean stop: (beats so far, interrupted)."""
    beats: list[Beat] = []
    user_cb = kw.pop("on_beat", None)

    def collect(b: Beat) -> None:
        beats.append(b)
        if user_cb:
            user_cb(b)

    try:
        asyncio.run(repeat(st, plan, on_beat=collect, **kw))
    except KeyboardInterrupt:
        sys.stderr.write("\nstopped (Ctrl-C)\n")
        return beats, True
    return beats, False


def stamp() -> str:
    return datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------ heartbeat
HEARTBEAT_PROMPT = (
    "Heartbeat check-in ({now}). Review the checklist below (and anything you know is pending: "
    "goals, kanban tasks, scheduled work). Only act on items that need attention now. If nothing "
    "needs attention, reply with exactly {ok} and nothing else; otherwise reply with a short "
    "report of what needs the user's attention.\n\n{checklist}"
)


def heartbeat_file(st: Settings, cwd: Path) -> Path | None:
    for path in (cwd / ".auoreo" / st.cli2.heartbeat_file, st.home / st.cli2.heartbeat_file):
        if path.is_file():
            return path
    return None


def heartbeat_prompt(st: Settings, checklist: str) -> str:
    body = checklist.strip() or "(no HEARTBEAT.md checklist; do a general check-in)"
    return HEARTBEAT_PROMPT.format(now=stamp(), ok=st.cli2.heartbeat_ok_token, checklist=body)


def is_quiet(text: str, token: str) -> bool:
    return text.strip().strip("`*_. ").upper() == token.upper()
