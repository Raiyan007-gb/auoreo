"""Deterministic memory curator (spec §11.10, §2.11 "Skill-writer / curator").

No LLM: one pass over the active facts that
- merges near-duplicates into the newest copy (same normalized text, or an FTS match with
  ≥ 0.9 token overlap); the older copies are archived with `into` recorded,
- archives facts unused (`use_count == 0`) for `curator_archive_after_days`,
- reports (never touches) facts unused for `curator_stale_after_days`,
- never touches pinned facts and never deletes anything.

Every mutation goes to the ledger (`memory/.curator_ledger.jsonl`); each pass writes
`logs/curator/<YYYYMMDD-HHMMSS>/REPORT.md`. Scheduled passes are guarded: at least
`curator_min_idle_hours` since the last session activity and `curator_interval_hours`
since the last real pass (`memory/.curator_state.json`). Dry runs never update the state.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from auoreo.core.db import SessionRow
from auoreo.memory import ledger
from auoreo.memory.store import MemoryStore
from auoreo.schema.chat import CuratorContradiction, CuratorMerge, CuratorReport

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)
DAY = 86_400.0
STATE = ".curator_state.json"
DUP_OVERLAP = 0.9
FIRST_CHECK_S = 300.0
"""Grace after startup so short-lived CLI processes never run a pass."""
CHECK_EVERY_S = 3600.0


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def _words(text: str) -> set[str]:
    return set(normalize(text).split())


def overlap(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / max(len(wa), len(wb)) if wa and wb else 0.0


@dataclass
class Plan:
    scanned: int = 0
    merges: list[tuple[int, int]] = field(default_factory=list)  # (gone, keep)
    archive: list[tuple[int, str]] = field(default_factory=list)
    stale: list[int] = field(default_factory=list)
    skipped_pinned: int = 0
    texts: dict[int, str] = field(default_factory=dict)


def _duplicate_groups(store: MemoryStore, rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    by_id = {int(r["id"]): r for r in rows}
    parent = {i: i for i in by_id}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen: dict[str, int] = {}
    for fid, r in by_id.items():
        key = normalize(str(r["content"]))
        if key in seen:
            parent[find(fid)] = find(seen[key])
        else:
            seen[key] = fid
        for other in store.fts("fact", str(r["content"]), 10):
            if (
                other != fid
                and other in by_id
                and overlap(r["content"], by_id[other]["content"]) >= DUP_OVERLAP
            ):
                parent[find(fid)] = find(other)
    groups: dict[int, list[dict[str, Any]]] = {}
    for fid, r in by_id.items():
        groups.setdefault(find(fid), []).append(r)
    return [g for g in groups.values() if len(g) > 1]


def plan(store: MemoryStore, now: float, stale_days: float, archive_days: float) -> Plan:
    """Decide every action without mutating anything (runs in a worker thread)."""
    rows = store.fact_rows(active_only=True)
    out = Plan(scanned=len(rows), texts={int(r["id"]): str(r["content"]) for r in rows})
    gone: set[int] = set()
    for group in _duplicate_groups(store, rows):
        keep = max(group, key=lambda r: (r["created_at"], r["id"]))
        for r in group:
            if r is not keep and not r["pinned"]:
                out.merges.append((int(r["id"]), int(keep["id"])))
                gone.add(int(r["id"]))
    for r in rows:
        fid = int(r["id"])
        if fid in gone or r["use_count"]:
            continue
        age = now - float(r["last_used_at"] or r["created_at"])
        if age < stale_days * DAY:
            continue
        if r["pinned"]:
            out.skipped_pinned += 1
        elif age >= archive_days * DAY:
            out.archive.append((fid, f"unused for {int(age // DAY)} days"))
        else:
            out.stale.append(fid)
    return out


def apply(store: MemoryStore, p: Plan) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for gone, keep in p.merges:
        store.absorb(keep, gone)
        entries.append(ledger.entry("curator", "merge", gone, "near-duplicate", into=keep))
    for fid, reason in p.archive:
        if store.archive_fact(fid):
            entries.append(ledger.entry("curator", "archive", fid, reason))
    return entries


def pending_contradictions(home: Path, store: MemoryStore) -> list[tuple[int, int]]:
    """Flags from the ledger whose two facts are both still active."""
    pairs: list[tuple[int, int]] = []
    for e in ledger.read(home):
        if e.get("action") == "flag_contradiction" and isinstance(e.get("existing_id"), int):
            pair = (int(e["fact_id"]), int(e["existing_id"]))
            if pair not in pairs:
                pairs.append(pair)
    alive = store.active_ids(sorted({i for p in pairs for i in p}))
    return [p for p in pairs if p[0] in alive and p[1] in alive]


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= 100 else text[:99] + "…"


def render_report(rep: CuratorReport, texts: dict[int, str], when: datetime) -> str:
    def lines(ids: list[int]) -> list[str]:
        return [f"- fact {i}: {_clip(texts.get(i, '?'))}" for i in ids] or ["- none"]

    out = [
        f"# Curator report — {when:%Y-%m-%d %H:%M:%S} UTC{' (dry run)' if rep.dry_run else ''}",
        "",
        "Deterministic pass (no LLM). Nothing is deleted; archived facts stay searchable with",
        "`knowledge_search include_archived=true`.",
        "",
        f"- scanned: {rep.scanned}",
        f"- archived{' (candidates)' if rep.dry_run else ''}: {len(rep.archived)}",
        f"- stale (not touched): {len(rep.stale)}",
        f"- duplicates merged: {len(rep.duplicates_merged)}",
        f"- pinned skipped: {rep.skipped_pinned}",
        f"- pending contradictions: {len(rep.contradictions)}",
        f"- ledger: {rep.ledger_path}",
        "",
        "## Archived" + (" (candidates)" if rep.dry_run else ""),
        *lines(rep.archived),
        "",
        "## Stale",
        *lines(rep.stale),
        "",
        "## Merged duplicates",
        *(
            [
                f"- fact {m.fact_id} → {m.into}: {_clip(texts.get(m.fact_id, '?'))}"
                for m in rep.duplicates_merged
            ]
            or ["- none"]
        ),
        "",
        "## Pending contradictions",
        *([f"- fact {c.new_id} vs {c.existing_id}" for c in rep.contradictions] or ["- none"]),
    ]
    return "\n".join(out) + "\n"


def latest_report(home: Path) -> Path | None:
    base = home / "logs" / "curator"
    if not base.is_dir():
        return None
    found = sorted(p for p in base.glob("*/REPORT.md") if p.is_file())
    return found[-1] if found else None


class Curator:
    def __init__(self, services: Services) -> None:
        self.s = services
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    @property
    def home(self) -> Path:
        return self.s.settings.home

    @property
    def state_path(self) -> Path:
        return self.home / "memory" / STATE

    def read_state(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_state(self, ts: float) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"last_run": ts}) + "\n", encoding="utf-8")
        tmp.replace(self.state_path)

    async def last_activity(self) -> float | None:
        async with self.s.db.session() as s:
            last = (await s.execute(select(func.max(SessionRow.updated_at)))).scalar()
        if last is None:
            return None
        return (last if last.tzinfo else last.replace(tzinfo=UTC)).timestamp()

    async def guard(self, now: float) -> str | None:
        """Why a scheduled pass must not run now, or None."""
        cfg = self.s.settings.memory
        last_run = self.read_state().get("last_run")
        if isinstance(last_run, int | float) and now - last_run < cfg.curator_interval_hours * 3600:
            return f"last pass {(now - last_run) / 3600:.1f}h ago (interval {cfg.curator_interval_hours}h)"
        act = await self.last_activity()
        if act is not None and now - act < cfg.curator_min_idle_hours * 3600:
            return f"session activity {(now - act) / 3600:.1f}h ago (min idle {cfg.curator_min_idle_hours}h)"
        return None

    async def run(self, dry_run: bool = False, *, force: bool = False) -> CuratorReport:
        lp = str(ledger.ledger_path(self.home))
        mem = self.s.memory
        if mem is None:
            return CuratorReport(dry_run=dry_run, skipped="memory is disabled", ledger_path=lp)
        cfg = self.s.settings.memory
        async with self._lock:
            now = time.time()
            if not force and not dry_run:
                reason = await self.guard(now)
                if reason:
                    return CuratorReport(dry_run=dry_run, skipped=reason, ledger_path=lp)
            p = await mem._run(
                plan, mem.store, now, cfg.curator_stale_after_days, cfg.curator_archive_after_days
            )
            if not dry_run:
                ledger.append(self.home, await mem._run(apply, mem.store, p))
            flags = await mem._run(pending_contradictions, self.home, mem.store)
            rep = CuratorReport(
                dry_run=dry_run,
                scanned=p.scanned,
                archived=[fid for fid, _ in p.archive],
                stale=p.stale,
                skipped_pinned=p.skipped_pinned,
                duplicates_merged=[CuratorMerge(fact_id=g, into=k) for g, k in p.merges],
                contradictions=[CuratorContradiction(new_id=a, existing_id=b) for a, b in flags],
                ledger_path=lp,
            )
            when = datetime.fromtimestamp(now, UTC)
            path = self.home / "logs" / "curator" / f"{when:%Y%m%d-%H%M%S}" / "REPORT.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            rep.report_path = str(path)
            path.write_text(render_report(rep, p.texts, when), encoding="utf-8")
            if not dry_run:
                self._write_state(now)
            log.info("curator pass: %s", rep.model_dump(exclude={"ledger_path", "report_path"}))
            return rep

    def start_loop(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="memory-curator")

    async def _loop(self) -> None:
        await asyncio.sleep(FIRST_CHECK_S)
        while True:
            try:
                rep = await self.run()
                if rep.skipped:
                    log.debug("curator skipped: %s", rep.skipped)
            except Exception:  # a failed pass must not kill the loop
                log.exception("curator pass failed")
            await asyncio.sleep(CHECK_EVERY_S)

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None


def get(services: Services) -> Curator:
    """The running curator (`services.ext["curator"]`) or a fresh one for manual runs."""
    cur = services.ext.get("curator")
    return cur if isinstance(cur, Curator) else Curator(services)


async def start(services: Services) -> Curator:
    cur = Curator(services)
    if services.memory is not None and services.settings.memory.curator_enabled:
        cur.start_loop()
    return cur
