"""Prompt hook and text-stop gates (spec §8.5).

Before a turn completes without tool calls, the gates in `loop.stop_gates` run in order
(default `verify_on_stop → pre_verify → kanban`). The interim answer is already persisted;
a gate that fires appends a synthetic user nudge and the loop continues. If the model's
reply to a nudge is empty, the interim answer stands (`pending_verification_response`).

* `verify_on_stop`: the session has a plan with steps whose `done_when` is not checked
  off (status != completed) — nudge once to verify them.
* `pre_verify`: the `Stop` lifecycle hooks; a blocking hook appends its reason as a
  `<stop-hook>` message (up to MAX_STOP_BLOCKS per turn, the existing hook contract).
* `kanban`: the session holds claimed-but-unfinished kanban tasks — nudge once.

Gates never run after an API error (those raise before this point), so a failing call
cannot spiral into compact → error → hook.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from auoreo.runtime.budget import OUT_OF_BAND
from auoreo.runtime.hooks import hooks_of

if TYPE_CHECKING:
    from auoreo.runtime.runner import AgentRunner, SessionRuntime

MAX_STOP_BLOCKS = 3
VERIFY_NUDGE = (
    f"{OUT_OF_BAND} <stop-gate:verify_on_stop> Before finishing, verify the plan: these steps "
    "have acceptance criteria that are not checked off yet:\n{steps}\nRun the checks, update "
    "the plan statuses with update_plan, then give your final answer."
)
KANBAN_NUDGE = (
    f"{OUT_OF_BAND} <stop-gate:kanban> You still hold claimed kanban tasks that are not "
    "finished:\n{tasks}\nComplete or block each one (with a reason) before finishing."
)


async def prompt_hook(
    runner: AgentRunner, sid: str, agent: str, text: str
) -> tuple[str, dict[str, Any] | None]:
    """UserPromptSubmit: may rewrite the prompt or deny the turn (before it is persisted)."""
    hooks = hooks_of(runner.s)
    if hooks is None:
        return text, None
    out = await hooks.run(
        "UserPromptSubmit", session_id=sid, payload={"agent": agent, "text": text}, match_key=agent
    )
    if out.denied:
        return text, {"code": "HOOK_DENIED", "message": out.message}
    return (out.text if out.text is not None else text), None


class StopGates:
    """Per-turn gate state: at most one nudge per gate per turn (Stop hooks: MAX_STOP_BLOCKS)."""

    def __init__(self, runner: AgentRunner, rt: SessionRuntime, agent: str, run_id: str) -> None:
        self.runner = runner
        self.rt = rt
        self.agent = agent
        self.run_id = run_id
        self.fired: set[str] = set()
        self.stop_blocks = 0

    async def check(self, text: str) -> bool:
        """True when a gate appended a nudge (the loop continues), False to complete."""
        gates = {"verify_on_stop": self._verify, "pre_verify": self._stop_hook, "kanban": self._kanban}
        for name in self.runner.s.settings.loop.stop_gates:
            gate = gates.get(name)
            if gate is None or (name != "pre_verify" and name in self.fired):
                continue
            nudge = await gate(text)
            if nudge is None:
                continue
            self.fired.add(name)
            await self._append(nudge, name)
            return True
        return False

    async def _append(self, msg: str, gate: str) -> None:
        await self.runner._persist(self.rt, {"role": "user", "content": msg})
        data: dict[str, Any] = {"run_id": self.run_id, "role": "user", "content": msg, "stop_gate": gate}
        if gate == "pre_verify":
            data["stop_hook"] = True
        await self.runner.s.emit(self.rt.session_id, "message.created", data)

    async def _verify(self, _: str) -> str | None:
        open_steps = [
            f"- {s.get('step', '')} (done when: {s['done_when']})"
            for s in self.rt.state.plan
            if s.get("done_when") and s.get("status") != "completed"
        ]
        return VERIFY_NUDGE.format(steps="\n".join(open_steps)) if open_steps else None

    async def _stop_hook(self, text: str) -> str | None:
        hooks = hooks_of(self.runner.s)
        if hooks is None or self.stop_blocks >= MAX_STOP_BLOCKS:
            return None
        out = await hooks.run(
            "Stop",
            session_id=self.rt.session_id,
            payload={"agent": self.agent, "text": text, "stop_blocks": self.stop_blocks},
            match_key=self.agent,
        )
        if not out.blocked:
            return None
        self.stop_blocks += 1
        return f"<stop-hook>\n{out.message}\n</stop-hook>"

    async def _kanban(self, _: str) -> str | None:
        teams = (getattr(self.runner.s, "ext", None) or {}).get("teams")
        kanban = getattr(teams, "kanban", None)
        if kanban is None:
            return None
        rows = await kanban.list_tasks(status="claimed")
        mine = [r for r in rows if r.session_id == self.rt.session_id]
        if not mine:
            return None
        return KANBAN_NUDGE.format(tasks="\n".join(f"- #{r.id} {r.title}" for r in mine))
