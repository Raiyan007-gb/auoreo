"""Persistence for the bandit: `bandit_policy` (arm stats) and `bandit_outcomes` (rewarded turns)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select

from auoreo.core.db import BanditArmRow, BanditOutcomeRow, Database, utcnow
from auoreo.policy.config import Arm

MEANS_WINDOW = 200
"""Outcomes per context used for the running cost/latency means."""


class BanditStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def stats(self, context_key: str) -> dict[str, tuple[int, float]]:
        """`arm_key → (pulls, reward_sum)` for one context."""
        async with self.db.session() as s:
            q = select(BanditArmRow).where(BanditArmRow.context_key == context_key)
            return {r.arm_key: (r.pulls, r.reward_sum) for r in (await s.execute(q)).scalars()}

    async def means(self, context_key: str) -> tuple[float, float]:
        """Running (mean cost USD, mean latency ms) over the context's recent outcomes."""
        async with self.db.session() as s:
            q = (
                select(BanditOutcomeRow.signals)
                .where(BanditOutcomeRow.context_key == context_key)
                .order_by(BanditOutcomeRow.id.desc())
                .limit(MEANS_WINDOW)
            )
            rows = list((await s.execute(q)).scalars())
        if not rows:
            return 0.0, 0.0
        cost = sum(float(r.get("cost_usd") or 0.0) for r in rows) / len(rows)
        latency = sum(float(r.get("latency_ms") or 0.0) for r in rows) / len(rows)
        return cost, latency

    async def record(
        self,
        *,
        session_id: str,
        run_id: str,
        context_key: str,
        arm: Arm,
        reward: float,
        signals: dict[str, Any],
    ) -> None:
        """Persist the outcome row and upsert the arm (pulls += 1, reward_sum += reward)."""
        async with self.db.session() as s:
            s.add(
                BanditOutcomeRow(
                    session_id=session_id,
                    run_id=run_id,
                    context_key=context_key,
                    arm_key=arm.key,
                    reward=reward,
                    signals=signals,
                )
            )
            row = await self._arm(s, context_key, arm.key)
            if row is None:
                row = BanditArmRow(context_key=context_key, arm_key=arm.key, pulls=0, reward_sum=0.0)
                s.add(row)
            row.arm = arm.model_dump()
            row.pulls = (row.pulls or 0) + 1
            row.reward_sum = (row.reward_sum or 0.0) + reward
            row.updated_at = utcnow()
            await s.commit()

    async def correct(self, session_id: str, penalty: float) -> BanditOutcomeRow | None:
        """Lower the session's latest outcome once (a correction cue in the next prompt):
        `corrections = 1`, reward and the arm's reward_sum drop by `penalty`."""
        async with self.db.session() as s:
            q = (
                select(BanditOutcomeRow)
                .where(BanditOutcomeRow.session_id == session_id)
                .order_by(BanditOutcomeRow.id.desc())
                .limit(1)
            )
            out = (await s.execute(q)).scalars().first()
            if out is None or (out.signals or {}).get("corrections"):
                return None
            out.signals = {**(out.signals or {}), "corrections": 1}
            out.reward = out.reward - penalty
            arm = await self._arm(s, out.context_key, out.arm_key)
            if arm is not None:
                arm.reward_sum -= penalty
                arm.updated_at = utcnow()
            await s.commit()
            await s.refresh(out)
            return out

    async def all_stats(self) -> list[BanditArmRow]:
        async with self.db.session() as s:
            q = select(BanditArmRow).order_by(BanditArmRow.context_key, BanditArmRow.arm_key)
            return list((await s.execute(q)).scalars())

    async def recent(self, limit: int = 20, context_key: str | None = None) -> list[BanditOutcomeRow]:
        async with self.db.session() as s:
            q = select(BanditOutcomeRow).order_by(BanditOutcomeRow.id.desc()).limit(limit)
            if context_key is not None:
                q = q.where(BanditOutcomeRow.context_key == context_key)
            return list((await s.execute(q)).scalars())

    async def reset(self, context_key: str | None = None) -> tuple[int, int]:
        """Delete arm stats and outcomes (one context or all); returns (arms, outcomes)."""
        arms_q = delete(BanditArmRow)
        outs_q = delete(BanditOutcomeRow)
        if context_key is not None:
            arms_q = arms_q.where(BanditArmRow.context_key == context_key)
            outs_q = outs_q.where(BanditOutcomeRow.context_key == context_key)
        async with self.db.session() as s:
            arms = (await s.execute(arms_q)).rowcount  # type: ignore[attr-defined]
            outs = (await s.execute(outs_q)).rowcount  # type: ignore[attr-defined]
            await s.commit()
        return int(arms or 0), int(outs or 0)

    @staticmethod
    async def _arm(s: Any, context_key: str, arm_key: str) -> BanditArmRow | None:
        q = select(BanditArmRow).where(
            BanditArmRow.context_key == context_key, BanditArmRow.arm_key == arm_key
        )
        return (await s.execute(q)).scalars().first()  # type: ignore[no-any-return]


__all__ = ["BanditStore"]
