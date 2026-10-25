"""Contextual epsilon-greedy model/effort policy (W1-C, spec §12.5)."""

from __future__ import annotations

import random
from typing import Any

import pytest
from sqlalchemy import select

from auoreo.core.db import BanditArmRow, BanditOutcomeRow
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.policy.bandit import (
    choose,
    context_key,
    current_epsilon,
    derive_arms,
    is_correction,
    reward,
    signals_of,
)
from auoreo.policy.config import Arm, BanditSettings, BanditWeights
from auoreo.schema.events import ServerEvent

A = Arm(model="small", effort=None)
B = Arm(model="default", effort="high")


class FixedRng(random.Random):
    """`random()` returns the queued values; `choice` picks the queued index."""

    def __init__(self, values: list[float], picks: list[int] | None = None) -> None:
        super().__init__(0)
        self.values, self.picks = list(values), list(picks or [])

    def random(self) -> float:
        return self.values.pop(0)

    def choice(self, seq: Any) -> Any:
        return seq[self.picks.pop(0)]


def enable(svc: Services, **kw: Any) -> None:
    svc.settings.bandit = BanditSettings(enabled=True, arms=[A, B], **kw)


async def turn(svc: Services, sid: str, text: str, **kw: Any) -> tuple[str, list[ServerEvent]]:
    sub = svc.bus.subscribe(frozenset({f"session:{sid}"}))
    rid = await svc.coordinator.submit(sid, text, interactive=False, **kw)
    await svc.coordinator.wait(sid, 10)
    events = []
    while not sub.queue.empty():
        events.append(sub.queue.get_nowait())
    result = svc.coordinator.result(rid or "")
    assert result is not None
    return result.status, events


def choices(events: list[ServerEvent]) -> list[dict[str, Any]]:
    return [e.data for e in events if e.type == "policy.choice"]


async def outcomes(svc: Services) -> list[BanditOutcomeRow]:
    async with svc.db.session() as s:
        return list((await s.execute(select(BanditOutcomeRow).order_by(BanditOutcomeRow.id))).scalars())


async def arm_rows(svc: Services) -> dict[str, BanditArmRow]:
    async with svc.db.session() as s:
        return {r.arm_key: r for r in (await s.execute(select(BanditArmRow))).scalars()}


# ----------------------------------------------------------------- pure parts
def test_epsilon_decays_per_100_pulls_to_the_floor() -> None:
    cfg = BanditSettings()
    assert current_epsilon(cfg, 0) == pytest.approx(0.15)
    assert current_epsilon(cfg, 99) == pytest.approx(0.15)
    assert current_epsilon(cfg, 100) == pytest.approx(0.13)
    assert current_epsilon(cfg, 650) == pytest.approx(0.03)
    assert current_epsilon(cfg, 10_000) == pytest.approx(0.02)


def test_seeded_rng_explores_below_epsilon_and_exploits_above() -> None:
    cfg = BanditSettings(enabled=True, arms=[A, B])
    stats = {A.key: (10, 9.0), B.key: (10, 2.0)}
    explore = choose(cfg, [A, B], stats, FixedRng([0.01], [1]), "agent:x")
    assert explore.explore and explore.arm == B and explore.why.startswith("explore")
    exploit = choose(cfg, [A, B], stats, FixedRng([0.99]), "agent:x")
    assert not exploit.explore and exploit.arm == A and "0.90" in exploit.why
    # untried arms are tried first when exploiting
    first = choose(cfg, [A, B], {A.key: (3, 3.0)}, FixedRng([0.99]))
    assert first.arm == B and "untried" in first.why
    # a real seeded RNG is reproducible
    picks = [choose(cfg, [A, B], stats, random.Random(7)).arm for _ in range(3)]
    assert picks == [choose(cfg, [A, B], stats, random.Random(7)).arm for _ in range(3)]


def test_reward_math_weights_success_highest() -> None:
    w = BanditWeights()
    sig = {
        "success": 1,
        "norm_cost": 1.0,
        "norm_latency": 0.5,
        "cache_hit": 1,
        "fallbacks": 1,
        "corrections": 0,
    }
    assert reward(w, sig) == pytest.approx(1.0 - 0.3 - 0.1 + 0.1 - 0.2)
    assert reward(w, {**sig, "success": 0}) < 0 < reward(w, sig)
    assert reward(w, {**sig, "corrections": 1}) == pytest.approx(reward(w, sig) - 0.3)


class _Row:
    def __init__(self, cost: float, latency: int, outcome: str | None, cached: int = 0) -> None:
        self.cost_usd, self.latency_ms, self.outcome, self.cached_tokens = cost, latency, outcome, cached


def test_signals_normalise_by_running_means_and_count_fallbacks() -> None:
    rows = [_Row(0.02, 300, "ok", cached=5), _Row(0.0, 100, "RATE_LIMIT")]
    sig = signals_of("completed", rows, (0.01, 200.0), n_prior=1)
    assert sig["success"] == 1 and sig["fallbacks"] == 1 and sig["cache_hit"] == 1
    assert sig["cost_usd"] == pytest.approx(0.02) and sig["latency_ms"] == 400
    assert sig["norm_cost"] == pytest.approx(0.02 / 0.015, rel=1e-3)
    assert sig["norm_latency"] == pytest.approx(400 / 300, rel=1e-3)
    assert signals_of("failed", [], (0.0, 0.0), 0)["success"] == 0


def test_correction_cues_contexts_and_derived_arms(settings: Settings) -> None:
    for text in ("no, use the other file", "that's wrong", "That’s not it", "undo that", "Try again"):
        assert is_correction(text), text
    for text in ("now do the next step", "know-how", "thanks"):
        assert not is_correction(text), text
    assert context_key("agent", "default", "x") == "agent:default"
    assert context_key("agent+size", "default", "x" * 600) == "agent:default|size:m"
    arms = derive_arms(settings)
    assert [a.key for a in arms] == ["default|-|-", "default|high|-", "small|-|-", "small|high|-"]


# ------------------------------------------------------------- with the runner
async def test_disabled_means_no_choice_no_events_no_rows(services: Services, gateway: FakeGateway) -> None:
    sess = await services.create_session()
    status, events = await turn(services, sess.id, "hello")
    assert status == "completed"
    assert choices(events) == [] and await outcomes(services) == []
    assert gateway.efforts == [None]


async def test_same_arm_across_the_steps_of_one_turn_and_outcome_persisted(
    services: Services, gateway: FakeGateway
) -> None:
    enable(services)
    services.coordinator.runner.policy.rng = FixedRng([0.01], [1])  # explore → B (high effort)
    gateway.queue(Scripted(tool_calls=[("glob", {"pattern": "*"})]), Scripted(text="done"))
    sess = await services.create_session()
    status, events = await turn(services, sess.id, "look around")
    assert status == "completed"
    [choice] = choices(events)
    assert choice["arm"]["key"] == B.key and choice["explore"] is True and choice["epsilon"] == 0.15
    assert [e.type for e in events].index("policy.choice") < [e.type for e in events].index("run.started")
    main = [r for r in gateway.requests if "purpose" not in r.metadata]
    assert len(main) == 2 and {r.model for r in main} == {"default"} and gateway.efforts == ["high", "high"]
    [out] = await outcomes(services)
    assert out.arm_key == B.key and out.context_key == "agent:default"
    assert out.signals["success"] == 1 and out.signals["calls"] == 2 and out.signals["fallbacks"] == 0
    assert out.reward == pytest.approx(1.0)  # fake gateway: zero cost; latency normalises to ≤ 1
    rows = await arm_rows(services)
    assert rows[B.key].pulls == 1 and rows[B.key].arm["effort"] == "high"


async def test_correction_in_next_prompt_lowers_previous_outcome(services: Services) -> None:
    enable(services)
    services.coordinator.runner.policy.rng = FixedRng([0.99, 0.99])
    sess = await services.create_session()
    await turn(services, sess.id, "write it")
    [before] = await outcomes(services)
    arm_before = (await arm_rows(services))[before.arm_key].reward_sum
    await turn(services, sess.id, "no, that's wrong")
    first, second = await outcomes(services)
    assert first.signals["corrections"] == 1
    assert first.reward == pytest.approx(before.reward - 0.3)
    assert second.signals["corrections"] == 0
    arms = await arm_rows(services)
    total = sum(r.reward_sum for r in arms.values())
    assert total == pytest.approx(arm_before - 0.3 + second.reward)
    # a second cue does not lower the same (now corrected) outcome twice
    corrected = await services.coordinator.runner.policy.store.correct(sess.id, 0.3)
    assert corrected is not None and corrected.id == second.id
    assert await services.coordinator.runner.policy.store.correct(sess.id, 0.3) is None


async def test_explicit_model_override_bypasses_the_bandit(services: Services, gateway: FakeGateway) -> None:
    enable(services)
    sess = await services.create_session(model="small")
    _, events = await turn(services, sess.id, "hi")
    sess2 = await services.create_session()
    _, events2 = await turn(services, sess2.id, "hi", model="small")
    assert choices(events) == [] and choices(events2) == []
    assert await outcomes(services) == []
    assert gateway.efforts == [None, None]


async def test_kill_switch_is_immediate(services: Services, gateway: FakeGateway) -> None:
    enable(services)
    policy = services.coordinator.runner.policy
    policy.rng = FixedRng([0.99])
    sess = await services.create_session()
    _, events = await turn(services, sess.id, "one")
    assert len(choices(events)) == 1
    services.settings.bandit.enabled = False
    _, events = await turn(services, sess.id, "two")
    assert choices(events) == [] and gateway.efforts[-1] is None
    # an in-flight turn chosen before the switch flips records nothing
    services.settings.bandit.enabled = True
    policy.rng = FixedRng([0.99])
    assert await policy.begin(sess.id, "default", None, "run_inflight", "three") is not None
    services.settings.bandit.enabled = False
    assert await policy.finish(sess.id, "run_inflight", "completed") is None
    assert len(await outcomes(services)) == 1


async def test_five_turns_learn_and_status_reports(services: Services) -> None:
    enable(services)
    sess = await services.create_session()
    services.coordinator.runner.policy.rng = random.Random(3)
    for i in range(5):
        await turn(services, sess.id, f"turn {i}")
    status = await services.coordinator.runner.policy.status()
    assert status["enabled"] and [c["pulls"] for c in status["contexts"]] == [5]
    assert {s["key"] for s in status["stats"]} == {A.key, B.key}  # both arms were tried
    assert len(status["recent"]) == 5
    arms, outs = await services.coordinator.runner.policy.store.reset("agent:other")
    assert (arms, outs) == (0, 0)
    arms, outs = await services.coordinator.runner.policy.store.reset()
    assert (arms, outs) == (2, 5)


async def test_arm_top_k_sets_the_memory_recall_depth(
    services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    services.settings.bandit = BanditSettings(enabled=True, arms=[Arm(model="default", top_k=3)])
    seen: list[int] = []

    class Mem:
        async def context_block(self, query: str, *, allow_aux: bool = True, limit: int = 8) -> str:
            seen.append(limit)
            return ""

        async def record_message(self, *a: Any, **kw: Any) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(services, "memory", Mem())
    sess = await services.create_session()
    await turn(services, sess.id, "hi")
    services.settings.bandit.enabled = False
    await turn(services, sess.id, "again")
    assert seen == [3, 8]
