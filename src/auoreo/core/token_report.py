"""`auoreo stats --token-economy`: the M7 W1 Wave 2 measurement gates (read-only).

One function per gate of the design's "Measurements and gates" table
(`docs/designs/m7-token-economy.md`, "Wave 2 implementation notes"). Each returns the measured
value, the threshold, the sample count and a verdict: `pass`, `fail` or `insufficient data`
(fewer samples than the stated minimum). No verdict changes a setting.

Inputs are main-turn data only (`core.token_report_data`). `h(call) = cached / prompt`; the
"first call" of a turn is its first main-turn usage row, the others are "tail calls".
The cache multipliers in M3 are configurable (`m7.token.mask.cache_read_multiplier`,
`cache_write_multiplier`); the defaults are Anthropic's documented 0.1x read and 1.25x (5m) /
2x (1h) write, and some models read at another rate.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from auoreo.core.token_report_data import Data, Turn, load
from auoreo.gateway.pipeline import family_of
from auoreo.policy.route import RouteFacts, RouteRule, eligible
from auoreo.runtime.compaction_plan import SUMMARY_TAG
from auoreo.runtime.masking_plan import plan

if TYPE_CHECKING:
    from auoreo.core.db import Database
    from auoreo.core.settings import Settings
    from auoreo.core.usage import PriceFn

PASS, FAIL, INSUFFICIENT = "pass", "fail", "insufficient data"
TTL_S = {"5m": 300, "1h": 3600}
WRITE_MULTIPLIER = {"5m": 1.25, "1h": 2.0}
LARGE_PROMPT = 30_000
"""M1/M3: sessions whose largest prompt is at least this many tokens."""
MIN = {"B1": 100, "B2": 50, "T1": 50, "R": 200, "M": 30, "M2": 1, "C1": 200}
"""Minimum samples per gate (R1-R3 share one, M1/M3 share one)."""


@dataclass
class Gate:
    id: str
    measurement: str
    value: float | None
    threshold: str
    samples: int
    min_samples: int
    verdict: str
    decides: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Report:
    days: int
    since: str
    flags: dict[str, Any]
    gates: list[Gate]
    notes: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _verdict(samples: int, minimum: int, ok: bool) -> str:
    return INSUFFICIENT if samples < minimum else (PASS if ok else FAIL)


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _anthropic(model: str) -> bool:
    return family_of(model) == "anthropic"


# ------------------------------------------------------------------ baseline
def gate_b1(d: Data) -> Gate:
    turns = sum(1 for t in d.turns.values() if t.summary is not None)
    value = 100 * d.prefix_changes / turns if turns else None
    return Gate(
        "B1",
        "cache.prefix_changed per 100 turns",
        value,
        "<= 2",
        turns,
        MIN["B1"],
        _verdict(turns, MIN["B1"], value is not None and value <= 2),
        "everything below (fix the prefix first when it fails)",
        {"prefix_changed": d.prefix_changes},
    )


def tail_hits(d: Data) -> list[float]:
    return [c.hit for t in d.turns.values() for c in t.calls[1:] if _anthropic(c.model)]


def gate_b2(d: Data) -> Gate:
    hits = tail_hits(d)
    value = _median(hits)
    ok = value is not None and value >= 0.5
    return Gate(
        "B2",
        "median h of tail calls (Anthropic family)",
        value,
        ">= 0.5 means the cache works",
        len(hits),
        MIN["B2"],
        _verdict(len(hits), MIN["B2"], ok),
        "M3, T1",
    )


def gate_t1(d: Data, ttl: str) -> Gate:
    """First calls of turns 2+ whose previous call in the session is younger than the TTL:
    measured `h` against the expected `P_prev / P` (the previous request is a prefix)."""
    measured: list[float] = []
    expected: list[float] = []
    by_session: dict[str, list[Turn]] = {}
    for t in d.turns.values():
        by_session.setdefault(t.session_id, []).append(t)
    for turns in by_session.values():
        turns.sort(key=lambda t: t.calls[0].id)
        for prev, cur in zip(turns, turns[1:], strict=False):
            first, last = cur.calls[0], prev.calls[-1]
            young = (first.at - last.at).total_seconds() < TTL_S[ttl]
            if young and _anthropic(first.model) and first.prompt > 0:
                measured.append(first.hit)
                expected.append(min(1.0, last.prompt / first.prompt))
    m, e = _median(measured), _median(expected)
    value = m / e if m is not None and e else None
    return Gate(
        "T1",
        "median h of first calls (turns 2+, within TTL) / median expected P_prev/P",
        value,
        ">= 0.8: no previous-turn breakpoint needed (below 0.8 with B1 passing: reopen `turn_start`)",
        len(measured),
        MIN["T1"],
        _verdict(len(measured), MIN["T1"], value is not None and value >= 0.8),
        "previous-turn breakpoint",
        {"median_measured": m, "median_expected": e},
    )


# ------------------------------------------------------------------ route
def _history_tokens(rows: list[tuple[dict[str, Any], Any]], t: Turn) -> int:
    """Estimated tokens of the history before the turn's user message (chars // 4)."""
    cut = len(rows)
    for i in range(len(rows) - 1, -1, -1):
        msg, at = rows[i]
        if (
            msg.get("role") == "user"
            and msg.get("content") == t.text
            and (t.asked_at is None or at <= t.asked_at)
        ):
            cut = i
            break
    return sum(len(str(m.get("content") or "")) for m, _ in rows[:cut]) // 4


async def eligible_turns(d: Data, rule: RouteRule) -> list[Turn]:
    out: list[Turn] = []
    cache: dict[str, list[tuple[dict[str, Any], Any]]] = {}
    for t in d.turns.values():
        if t.text is None:
            continue
        sess = d.sessions.get(t.session_id)
        facts = RouteFacts(t.text, t.agent, 0, override=bool(sess and sess.model))
        if eligible(facts, rule) is None:  # cheap checks first; history only when needed
            continue
        if t.session_id not in cache:
            cache[t.session_id] = await d.messages(t.session_id)
        facts = RouteFacts(t.text, t.agent, _history_tokens(cache[t.session_id], t), override=facts.override)
        if eligible(facts, rule) is not None:
            out.append(t)
    return out


def _used_tools(t: Turn) -> bool:
    s = t.summary or {}
    return int(s.get("tools") or 0) > 0 or int(s.get("steps") or len(t.calls)) > 1


def route_gates(d: Data, turns: list[Turn], price: PriceFn, cheap: str | None) -> list[Gate]:
    n = len(turns)
    total = sum(c.cost for c in d.calls)
    share = sum(t.cost for t in turns) / total if total > 0 else None
    r1 = Gate(
        "R1",
        "cost share of route-eligible turns in main-turn cost",
        share,
        ">= 0.05 and >= 200 eligible turns",
        n,
        MIN["R"],
        _verdict(n, MIN["R"], share is not None and share >= 0.05),
        "route worth building",
    )
    q = sum(1 for t in turns if _used_tools(t)) / n if n else None
    r2 = Gate(
        "R2",
        "proxy rerun rate q (default model used a tool or more than one step)",
        q,
        "<= 0.30",
        n,
        MIN["R"],
        _verdict(n, MIN["R"], q is not None and q <= 0.30),
        "route",
    )
    return [r1, r2, _gate_r3(turns, price, cheap, q or 0.0)]


def _gate_r3(turns: list[Turn], price: PriceFn, cheap: str | None, q: float) -> Gate:
    default = sum(t.cost for t in turns)
    cold = warm = 0.0
    priced = 0
    for t in turns:
        f = t.calls[0]
        try:
            c = price(cheap, f.prompt, f.completion, cache_write_tokens=f.prompt) if cheap else None
            w = (
                price(cheap, f.prompt, f.completion, cached_tokens=f.cached, cache_write_tokens=f.cache_write)
                if cheap
                else None
            )
        except Exception:
            c = w = None
        if c is None or w is None:
            continue
        priced, cold, warm = priced + 1, cold + c, warm + w
    saving = {
        name: (1 - (cheap_cost + q * default) / default) if default > 0 and priced else None
        for name, cheap_cost in (("cold", cold), ("warm", warm))
    }
    s_cold, s_warm = saving["cold"], saving["warm"]
    action = (
        "enable"
        if s_cold is not None and s_cold >= 0.20
        else "trial (R4 decides)"
        if s_warm is not None and s_warm >= 0.20
        else "do not build"
    )
    verdict = _verdict(priced, MIN["R"], action != "do not build")
    return Gate(
        "R3",
        "counterfactual saving 1 - (C_cheap + q*C_default) / C_default (cold bound)",
        s_cold,
        "cold >= 0.20: enable; only warm >= 0.20: trial; neither: do not build",
        priced,
        MIN["R"],
        verdict,
        "route",
        {"warm": s_warm, "action": action if verdict != INSUFFICIENT else None, "cheap_model": cheap},
    )


# ------------------------------------------------------------------ masking
async def mask_samples(d: Data) -> tuple[list[tuple[str, Any]], int]:
    """`(session id, (history, created_at list))` for main sessions whose largest prompt is
    LARGE_PROMPT or more and that hold no compaction summary; plus the count skipped."""
    largest: dict[str, int] = {}
    for c in d.calls:
        largest[c.session_id] = max(largest.get(c.session_id, 0), c.prompt)
    out: list[tuple[str, Any]] = []
    skipped = 0
    for sid in sorted(s for s, p in largest.items() if p >= LARGE_PROMPT):
        rows = await d.messages(sid)
        history = [m for m, _ in rows]
        if any(
            m.get("role") == "user" and str(m.get("content") or "").startswith(SUMMARY_TAG) for m in history
        ):
            skipped += 1  # already compacted: the stored history under-reports
            continue
        out.append((sid, (history, [at for _, at in rows])))
    return out, skipped


def gate_m1(samples: list[tuple[str, Any]], settings: Settings) -> Gate:
    cfg = settings.m7.token.mask
    shares = []
    for _, (history, _) in samples:
        total = sum(len(str(m.get("content") or "")) for m in history) // 4
        if total > 0:
            shares.append(plan(history, cfg).tokens / total)
    value = _median(shares)
    return Gate(
        "M1",
        "median maskable share M/total (sessions with a prompt of 30k+ tokens)",
        value,
        ">= 0.25 and >= 30 sessions",
        len(shares),
        MIN["M"],
        _verdict(len(shares), MIN["M"], value is not None and value >= 0.25),
        "masking worth building",
    )


def gate_m2(d: Data) -> Gate:
    n = len(d.compactions)
    hits = sum(
        1
        for c in d.compactions
        if c.get("source") == "summary" and c.get("trigger") in ("pressure", "hygiene")
    )
    value = hits / n if n else None
    return Gate(
        "M2",
        "share of compactions that called the summarizer under pressure or hygiene",
        value,
        "> 0 means the free mask path has work",
        n,
        MIN["M2"],
        _verdict(n, MIN["M2"], value is not None and value > 0),
        'masking = "compaction"',
    )


def break_even(m: int, s: int, h: float, r: float, w: float) -> float:
    """`N* = S*h*(w-r) / (M*(h*r + 1 - h))`: steps after which masking `M` tokens pays back
    rewriting `S` cached tokens."""
    denominator = m * (h * r + 1 - h)
    return s * h * (w - r) / denominator if denominator > 0 else float("inf")


def _first_over(history: list[dict[str, Any]], settings: Settings) -> int | None:
    """Smallest prefix length whose maskable tokens reach `mask.min_tokens` (binary search;
    the maskable size grows with the history, so this is a close estimate)."""
    cfg = settings.m7.token.mask
    if plan(history, cfg).tokens < cfg.min_tokens:
        return None
    lo, hi = 1, len(history)
    while lo < hi:
        mid = (lo + hi) // 2
        if plan(history[:mid], cfg).tokens >= cfg.min_tokens:
            hi = mid
        else:
            lo = mid + 1
    return lo


def gate_m3(d: Data, samples: list[tuple[str, Any]], settings: Settings, h: float | None) -> Gate:
    mask = settings.m7.token.mask
    ttl = settings.gateway_tuning.cache_ttl
    r = mask.cache_read_multiplier
    w = mask.cache_write_multiplier if mask.cache_write_multiplier is not None else WRITE_MULTIPLIER[ttl]
    calls: list[float] = []
    stars: list[float] = []
    for sid, (history, times) in samples if h is not None else []:
        k = _first_over(history, settings)
        if k is None:
            continue
        p = plan(history[:k], mask)
        at = times[k - 1]
        calls.append(sum(1 for c in d.calls if c.session_id == sid and c.at > at))
        stars.append(break_even(p.tokens, p.suffix_tokens, h or 0.0, r, w))
    mc, ms = _median(calls), _median(stars)
    ok = mc is not None and ms is not None and mc >= 2 * ms
    return Gate(
        "M3",
        "median main-turn calls after maskable tokens first reach mask.min_tokens",
        mc,
        ">= 2 * N* (median break-even)",
        len(calls),
        MIN["M"],
        _verdict(len(calls), MIN["M"], ok),
        'masking = "economic" (not built)',
        {"median_break_even": ms, "h": h, "read_multiplier": r, "write_multiplier": w},
    )


# ------------------------------------------------------------------ bandit
def gate_c1(d: Data, cached_pricing: bool) -> Gate:
    full = {k: v for k, v in d.outcomes.items() if len(v) >= MIN["C1"]}
    passing = []
    for key, rows in full.items():
        share = sum(1 for s in rows if s.get("cache_hit")) / len(rows)
        ratios = [float(s["cache_ratio"]) for s in rows if "cache_ratio" in s]
        spread = statistics.pstdev(ratios) if len(ratios) > 1 else 0.0
        if share >= 0.95 and spread >= 0.15:
            passing.append(key)
    ok = bool(passing) and not cached_pricing
    return Gate(
        "C1",
        "bandit contexts (last 200 outcomes) with cache_hit share >= 0.95 and cache_ratio stdev >= 0.15",
        float(len(passing)),
        "at least one such context and cached_pricing off",
        len(full),
        1,
        INSUFFICIENT if not full else (PASS if ok else FAIL),
        'bandit_cache_signal = "ratio"',
        {"contexts": passing, "cached_pricing": cached_pricing},
    )


# ------------------------------------------------------------------ report
def _rule(settings: Settings) -> RouteRule:
    return RouteRule(groups=frozenset(settings.models))


def _cheap_model(settings: Settings, rule: RouteRule) -> str | None:
    group = settings.models.get(rule.model)
    return group.models[0] if group and group.models else None


async def build(db: Database, settings: Settings, price: PriceFn, days: int) -> Report:
    d = await load(db, days)
    token = settings.m7.token
    rule = _rule(settings)
    b2 = gate_b2(d)
    samples, skipped = await mask_samples(d)
    gates = [
        gate_b1(d),
        b2,
        gate_t1(d, settings.gateway_tuning.cache_ttl),
        *route_gates(d, await eligible_turns(d, rule), price, _cheap_model(settings, rule)),
        gate_m1(samples, settings),
        gate_m2(d),
        gate_m3(d, samples, settings, b2.value),
        gate_c1(d, token.cached_pricing),
    ]
    flags = {
        "telemetry": token.telemetry,
        "cache_layout": token.cache_layout,
        "context_carrier": token.context_carrier,
        "cached_pricing": token.cached_pricing,
        "cache_ttl": settings.gateway_tuning.cache_ttl,
    }
    notes = {"main_turns": len(d.turns), "main_calls": len(d.calls), "sessions_skipped_compacted": skipped}
    return Report(days, d.since.isoformat(), flags, gates, notes)


__all__ = ["FAIL", "INSUFFICIENT", "PASS", "Gate", "Report", "break_even", "build"]
