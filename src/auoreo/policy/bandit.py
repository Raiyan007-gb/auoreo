"""Contextual epsilon-greedy model/effort policy (spec §12.5, §24.4). Default OFF.

Lifecycle, all at turn boundaries:
- `begin` (before the turn's spec is built): a correction cue in the new prompt lowers the
  session's previous outcome; then, when enabled and no explicit model override is set
  (session model, `--model`, `/model`), it picks one arm and emits `policy.choice`.
- the runner reads `model`/`effort` for that run on every step, so one turn never changes arm.
- `finish`: reward from the run's usage rows, persisted with the arm's stats.

`bandit.enabled: false` is the kill switch: checked on every `begin`/`finish`, so the next
turn uses static defaults and an in-flight turn records nothing.
"""

from __future__ import annotations

import logging
import random
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from auoreo.core.settings import Settings
from auoreo.core.usage import UsageLedger, cache_hit_ratio
from auoreo.policy.config import Arm, BanditSettings, BanditWeights
from auoreo.policy.store import BanditStore

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)
DEFAULT_GROUPS = ("default", "small")
DERIVED_EFFORTS = (None, "high")
NORM_CAP = 3.0
MAX_OPEN_TURNS = 256
CORRECTION = re.compile(r"^\s*no\s*,|\bwrong\b|\bthat(?:'|’)?s not\b|\bundo\b|\btry again\b", re.IGNORECASE)


@dataclass(frozen=True)
class Choice:
    arm: Arm
    explore: bool
    why: str
    epsilon: float
    context_key: str = ""


def is_correction(text: str) -> bool:
    return bool(CORRECTION.search(text or ""))


def derive_arms(settings: Settings) -> list[Arm]:
    """`bandit.arms`, or the `default` + `small` groups × effort {none, high}."""
    if settings.bandit.arms:
        return list(settings.bandit.arms)
    groups = [g for g in DEFAULT_GROUPS if g in settings.models] or ["default"]
    return [Arm(model=g, effort=e) for g in groups for e in DERIVED_EFFORTS]


def context_key(mode: str, agent: str, text: str) -> str:
    if mode != "agent+size":
        return f"agent:{agent}"
    size = "s" if len(text) < 500 else "m" if len(text) < 4000 else "l"
    return f"agent:{agent}|size:{size}"


def current_epsilon(cfg: BanditSettings, pulls: int) -> float:
    """`epsilon` minus `epsilon_decay` per 100 pulls, floored at `min_epsilon`."""
    return max(cfg.min_epsilon, cfg.epsilon - cfg.epsilon_decay * (pulls // 100))


def choose(
    cfg: BanditSettings,
    arms: list[Arm],
    stats: dict[str, tuple[int, float]],
    rng: random.Random,
    context: str = "",
) -> Choice:
    """Explore a uniformly random arm with probability ε; else exploit: an untried arm
    first (in configured order), otherwise the best mean reward (ties → configured order)."""
    eps = current_epsilon(cfg, sum(p for p, _ in stats.values()))
    if len(arms) > 1 and rng.random() < eps:
        arm = rng.choice(arms)
        return Choice(arm, True, f"explore: random arm (ε={eps:.2f})", eps, context)
    untried = [a for a in arms if stats.get(a.key, (0, 0.0))[0] == 0]
    if untried:
        return Choice(untried[0], False, f"exploit: untried arm (ε={eps:.2f})", eps, context)

    def mean(a: Arm) -> float:
        pulls, total = stats[a.key]
        return total / pulls

    best = max(arms, key=mean)
    why = f"exploit: best mean reward {mean(best):.2f} over {stats[best.key][0]} turns (ε={eps:.2f})"
    return Choice(best, False, why, eps, context)


def _norm(value: float, mean: float) -> float:
    if mean <= 0:
        return 0.0 if value <= 0 else 1.0
    return min(value / mean, NORM_CAP)


def reward(w: BanditWeights, signals: dict[str, Any], cache_signal: str = "binary") -> float:
    """`w1·success − w2·normCost − w3·normLatency + w4·cacheHit − w5·fallbacks − w6·corrections`.
    `cache_signal = "ratio"` (`m7.token.bandit_cache_signal`) uses `cache_ratio` as cacheHit."""
    cache = signals.get("cache_ratio", 0.0) if cache_signal == "ratio" else signals.get("cache_hit", 0)
    return (
        w.success * float(signals.get("success", 0))
        - w.cost * float(signals.get("norm_cost", 0.0))
        - w.latency * float(signals.get("norm_latency", 0.0))
        + w.cache_hit * float(cache)
        - w.fallbacks * min(float(signals.get("fallbacks", 0)), NORM_CAP)
        - w.corrections * float(signals.get("corrections", 0))
    )


def signals_of(status: str, rows: list[Any], means: tuple[float, float], n_prior: int) -> dict[str, Any]:
    """Signals for one run. Cost and latency are normalised by the context's running means
    (all arms, this run included) so the reward compares arms on a shared scale."""
    cost = sum(float(r.cost_usd or 0.0) for r in rows)
    latency = sum(int(r.latency_ms or 0) for r in rows)
    mean_cost = (means[0] * n_prior + cost) / (n_prior + 1)
    mean_latency = (means[1] * n_prior + latency) / (n_prior + 1)
    ok = [r for r in rows if r.outcome == "ok"]  # --- M7: main-turn rows, the Wave 1 definition
    prompt = sum(int(getattr(r, "prompt_tokens", 0) or 0) for r in ok)
    return {
        "status": status,
        "success": 1 if status == "completed" else 0,
        "cost_usd": cost,
        "latency_ms": latency,
        "norm_cost": round(_norm(cost, mean_cost), 4),
        "norm_latency": round(_norm(latency, mean_latency), 4),
        "cache_hit": 1 if any((r.cached_tokens or 0) > 0 for r in rows) else 0,
        "cache_ratio": round(cache_hit_ratio(sum(int(r.cached_tokens or 0) for r in ok), prompt), 4),
        "fallbacks": sum(1 for r in rows if r.outcome not in (None, "ok")),
        "corrections": 0,
        "calls": len(rows),
    }


class BanditPolicy:
    def __init__(self, services: Services, usage: UsageLedger, rng: random.Random | None = None) -> None:
        self.s = services
        self.usage = usage
        self.store = BanditStore(services.db)
        self.rng = rng or random.Random()
        self._turns: OrderedDict[str, Choice] = OrderedDict()

    @property
    def cfg(self) -> BanditSettings:
        return self.s.settings.bandit

    async def begin(
        self, session_id: str, agent: str, override: str | None, run_id: str, text: str
    ) -> Choice | None:
        """Never fails the turn: a policy error falls back to the static model."""
        if not self.cfg.enabled:
            return None
        try:
            return await self._begin(session_id, agent, override, run_id, text)
        except Exception:
            log.exception("bandit choice failed; using static defaults")
            return None

    async def _begin(
        self, session_id: str, agent: str, override: str | None, run_id: str, text: str
    ) -> Choice | None:
        cfg = self.cfg
        if is_correction(text):
            await self.store.correct(session_id, cfg.weights.corrections)
        if override:
            return None
        key = context_key(cfg.contexts, agent, text)
        choice = choose(cfg, derive_arms(self.s.settings), await self.store.stats(key), self.rng, key)
        self._turns[run_id] = choice
        while len(self._turns) > MAX_OPEN_TURNS:  # turns that never reached `finish`
            self._turns.popitem(last=False)
        await self.s.emit(
            session_id,
            "policy.choice",
            {
                "run_id": run_id,
                "context_key": key,
                "arm": {"key": choice.arm.key, **choice.arm.model_dump()},
                "explore": choice.explore,
                "why": choice.why,
                "epsilon": round(choice.epsilon, 4),
            },
        )
        return choice

    def effort(self, run_id: str, default: str | None) -> Any:
        """The arm's effort for a bandit-chosen run, else the agent manifest's."""
        choice = self._turns.get(run_id)
        return choice.arm.effort if choice is not None else default

    async def finish(self, session_id: str, run_id: str, status: str) -> float | None:
        choice = self._turns.pop(run_id, None)
        if choice is None or not self.cfg.enabled:
            return None
        try:
            return await self._finish(session_id, run_id, status, choice)
        except Exception:
            log.exception("bandit outcome failed")
            return None

    async def _finish(self, session_id: str, run_id: str, status: str, choice: Choice) -> float:
        rows = await self.usage.by_run(session_id, run_id)
        stats = await self.store.stats(choice.context_key)
        prior = min(sum(p for p, _ in stats.values()), 200)
        signals = signals_of(status, rows, await self.store.means(choice.context_key), prior)
        signals["explore"] = choice.explore
        value = round(reward(self.cfg.weights, signals, self.s.settings.m7.token.bandit_cache_signal), 6)
        await self.store.record(
            session_id=session_id,
            run_id=run_id,
            context_key=choice.context_key,
            arm=choice.arm,
            reward=value,
            signals=signals,
        )
        return value

    async def status(self) -> dict[str, Any]:
        cfg = self.cfg
        rows = await self.store.all_stats()
        pulls: dict[str, int] = {}
        for r in rows:
            pulls[r.context_key] = pulls.get(r.context_key, 0) + r.pulls
        return {
            "enabled": cfg.enabled,
            "epsilon": cfg.epsilon,
            "epsilon_decay": cfg.epsilon_decay,
            "min_epsilon": cfg.min_epsilon,
            "contexts_mode": cfg.contexts,
            "weights": cfg.weights.model_dump(),
            "arms": [{"key": a.key, **a.model_dump()} for a in derive_arms(self.s.settings)],
            "contexts": [
                {"context_key": k, "pulls": n, "epsilon": round(current_epsilon(cfg, n), 4)}
                for k, n in sorted(pulls.items())
            ],
            "stats": [
                {
                    "context_key": r.context_key,
                    "key": r.arm_key,
                    "model": str((r.arm or {}).get("model", r.arm_key.split("|")[0])),
                    "effort": (r.arm or {}).get("effort"),
                    "top_k": (r.arm or {}).get("top_k"),
                    "pulls": r.pulls,
                    "mean_reward": round(r.reward_sum / r.pulls, 4) if r.pulls else 0.0,
                    "updated_at": r.updated_at,
                }
                for r in rows
            ],
            "recent": [
                {
                    "id": o.id,
                    "session_id": o.session_id,
                    "run_id": o.run_id,
                    "context_key": o.context_key,
                    "arm_key": o.arm_key,
                    "reward": o.reward,
                    "signals": o.signals or {},
                    "created_at": o.created_at,
                }
                for o in await self.store.recent(20)
            ],
        }


def policy_of(services: Services) -> BanditPolicy:
    """The live runner's policy (shares its open-turn map) or a fresh one over the same DB."""
    coordinator = getattr(services, "coordinator", None)
    runner = getattr(coordinator, "runner", None)
    found = getattr(runner, "policy", None)
    if isinstance(found, BanditPolicy):
        return found
    return BanditPolicy(services, UsageLedger(services.db))


__all__ = [
    "BanditPolicy",
    "Choice",
    "choose",
    "context_key",
    "current_epsilon",
    "derive_arms",
    "is_correction",
    "policy_of",
    "reward",
    "signals_of",
]
