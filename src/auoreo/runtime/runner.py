"""The agent loop: one turn = steps of (model call → tool round) until a final answer.

Spec §8: freeze the request per step, persist the user batch before streaming, run
concurrency-safe tool calls in parallel and others as barriers, commit results in model
order, stop on doom/denial loops, and force a tool-free final step when the step cap or
the run budget runs out. Recovery (OTK/overflow/empty), budgets, liveness, stop gates,
loop guards and sequence repair live in their own modules next to this one.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import uuid
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import func, select

from auoreo.core import logjson
from auoreo.core import observability as obs
from auoreo.core.db import MessageRow
from auoreo.core.permissions import PermissionEngine
from auoreo.core.policy import policy_engine
from auoreo.core.usage import UsageLedger
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.types import LLMRequest, ToolCall, tool_schema
from auoreo.harness.loader import ToolDef
from auoreo.harness.orchestrator import AgentSpec, build_spec, floors, render_context
from auoreo.policy.bandit import BanditPolicy, Choice
from auoreo.runtime.budget import GRACE_NOTE, TurnBudget, all_free
from auoreo.runtime.compaction import CompactionError, CompactionService, turn_index
from auoreo.runtime.guards import LoopGuards
from auoreo.runtime.inbox import Inbox, RunOptions
from auoreo.runtime.liveness import LivenessWatchdog
from auoreo.runtime.multimodal import prepare_messages
from auoreo.runtime.recovery import model_step
from auoreo.runtime.sequence import repair
from auoreo.runtime.stopgates import StopGates, prompt_hook
from auoreo.runtime.tool_round import ABORTED_BEFORE_DISPATCH, tool_round
from auoreo.tools.context import SessionState, ToolContext
from auoreo.tools.executor import ToolExecutor
from auoreo.voice.tone import tone_of

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)

TurnStatus = Literal["completed", "blocked", "aborted", "failed", "max_steps"]
STEP_LIMIT_NOTE = (
    "You have reached the step limit for this turn. Do not call tools. "
    "Summarize what you did, what is left, and answer now."
)


@dataclass
class SessionRuntime:
    """Per-session live state, owned by the coordinator."""

    session_id: str
    agent: str
    workflow: str
    model: str | None
    history: list[dict[str, Any]] = field(default_factory=list)
    state: SessionState = field(default_factory=SessionState)
    inbox: Inbox = field(default_factory=Inbox)
    engine: PermissionEngine | None = None
    interactive: bool = True
    running: bool = False
    task: asyncio.Task[None] | None = None
    session_started: bool = False  # SessionStart hooks ran for this runtime
    subagent: bool = False  # a child session: IterationBudget = delegation_max_iterations
    generation: int = 0  # bumped per turn; liveness watchdogs of older turns stand down
    doom_exempt: set[str] = field(default_factory=set)  # tools the user said `always` for
    keep_inbox_on_cancel: bool = True


@dataclass
class TurnResult:
    run_id: str
    status: TurnStatus
    text: str
    error: dict[str, Any] | None = None
    truncated: bool = False  # a length cut that recovery could not finish


@dataclass
class Turn:
    """Live state of one running turn: budget/stats, liveness watchdog, latest answer text."""

    budget: TurnBudget
    watchdog: LivenessWatchdog
    text: str = ""


class AgentRunner:
    def __init__(self, services: Services) -> None:
        self.s = services
        self.executor = ToolExecutor(services)
        self.usage = UsageLedger(services.db, services.gateway.price)
        self.compaction = CompactionService(services, self.usage)
        self.policy = BanditPolicy(services, self.usage)  # --- W1-C (off unless bandit.enabled)

    # ------------------------------------------------------------ persistence
    async def load_history(self, session_id: str) -> list[dict[str, Any]]:
        async with self.s.db.session() as s:
            q = select(MessageRow).where(MessageRow.session_id == session_id).order_by(MessageRow.idx)
            return [dict(r.message) for r in (await s.execute(q)).scalars()]

    async def _persist(self, rt: SessionRuntime, message: dict[str, Any]) -> None:
        async with self.s.db.session() as s:
            last = (
                await s.execute(
                    select(func.max(MessageRow.idx)).where(MessageRow.session_id == rt.session_id)
                )
            ).scalar()
            s.add(
                MessageRow(
                    session_id=rt.session_id,
                    idx=(last if last is not None else -1) + 1,
                    message=message,
                )
            )
            await s.commit()
        rt.history.append(message)

    # ------------------------------------------------------------------ turn
    def _spec(
        self, rt: SessionRuntime, agent: str, model_override: str | None, choice: Choice | None = None
    ) -> AgentSpec:
        from auoreo.gateway.resolve import resolve_model

        manifest = self.s.harness.agent(agent).manifest
        agent_model = choice.arm.model if choice else manifest.model  # bandit arm, turn boundary only
        model = resolve_model(
            self.s.settings, session_override=model_override or rt.model, agent_model=agent_model
        )
        mode = manifest.permission_mode or self.s.settings.permission_mode
        return build_spec(self.s.harness, agent, model, mode)

    def _engine(self, rt: SessionRuntime, spec: AgentSpec) -> PermissionEngine:
        mode = spec.agent.manifest.permission_mode or self.s.settings.permission_mode
        plans = str(self.s.cwd / ".auoreo" / "plans")
        if rt.engine is None:
            rt.engine = PermissionEngine(list(spec.rules), floors(self.s.harness), mode, plans)
        else:  # keep session rules + always-grants, refresh definitions
            rt.engine.rules = list(spec.rules)
            rt.engine.floors = floors(self.s.harness)
            rt.engine.mode = "plan" if rt.state.plan_mode else mode
            rt.engine.__post_init__()
        rt.engine.policies = policy_engine(self.s.harness)  # child CeilingEngines get it here too
        rt.engine.context = {"agent": spec.name, "session_id": rt.session_id, "cwd": str(self.s.cwd)}
        if rt.state.plan_mode:
            rt.engine.mode = "plan"
        return rt.engine

    async def run_turn(
        self,
        rt: SessionRuntime,
        text: str,
        *,
        agent: str | None = None,
        model: str | None = None,
        run_id: str | None = None,
        options: RunOptions | None = None,
    ) -> TurnResult:
        run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
        sid = rt.session_id
        agent = agent or rt.agent
        options = options or RunOptions()
        try:
            choice = await self.policy.begin(sid, agent, model or rt.model, run_id, text)
            spec = self._spec(rt, agent, model, choice)
        except (KeyError, ValueError) as exc:
            await self.s.emit(sid, "run.failed", {"run_id": run_id, "code": "CONFIG", "message": str(exc)})
            return TurnResult(run_id, "failed", "", {"code": "CONFIG", "message": str(exc)})
        engine = self._engine(rt, spec)
        await self.s.emit(
            sid,
            "run.started",
            {
                "run_id": run_id,
                "agent": spec.name,
                "model": spec.model,
                "generation": self.s.harness.generation,
            },
        )
        text, hook_error = await prompt_hook(self, sid, spec.name, text)
        if hook_error is not None:
            await self.s.emit(sid, "run.blocked", {"run_id": run_id, "text": "", "error": hook_error})
            return TurnResult(run_id, "blocked", "", hook_error)
        await self.s.set_session_status(sid, "running", title=text)

        user_msg = {"role": "user", "content": text}
        await self._persist(rt, user_msg)
        await self.s.emit(sid, "message.created", {"run_id": run_id, "role": "user", "content": text})
        turn_msg = rt.history[-1]  # located by identity each step: compaction moves indices
        if self.s.memory is not None:
            await self.s.memory.record_message(sid, "user", text)

        memory_block = ""
        if self.s.memory is not None and spec.agent.manifest.memory and not options.skip_memory:
            try:
                top_k = (choice.arm.top_k if choice else None) or 8  # bandit arm may tune recall depth
                memory_block = await self.s.memory.context_block(text, limit=top_k)
            except Exception:  # recall must never fail the turn
                log.exception("memory recall failed")
        st = self.s.settings
        context = render_context(
            spec, self.s.cwd, engine.mode, memory_block, st.locale or "", tone_of(text, st)
        )
        rt.generation += 1  # the liveness watchdog is bound to this turn's generation
        turn = Turn(TurnBudget.for_turn(st.loop, subagent=rt.subagent), LivenessWatchdog(self.s, rt, run_id))

        async def question(qs: list[dict[str, Any]]) -> dict[str, Any]:
            return await self.s.questions.ask(sid, qs)

        async def emit(event_type: str, data: dict[str, Any]) -> None:
            turn.watchdog.touch()  # tool progress counts as liveness
            await self.s.emit(sid, event_type, {"run_id": run_id, **data})

        ctx = ToolContext(
            session_id=sid,
            cwd=self.s.cwd,
            home=self.s.settings.home,
            harness=self.s.harness,
            state=rt.state,
            interactive=rt.interactive,
            question=question,
            emit=emit,
            services={"memory": self.s.memory, "services": self.s, "engine": engine, "spec": spec},
        )
        with (
            obs.span("agent.run", model=spec.model, agent=spec.name) as span,  # --- W0-D
            logjson.bound(session_id=sid, run_id=run_id),  # --- G5c: JSON log linkage
        ):
            result = await self._watched(rt, spec, engine, ctx, run_id, context, turn_msg, options, turn)
            span.set("status", result.status)
            obs.count("run_completed_total", status=result.status)
        await self.policy.finish(sid, run_id, result.status)
        if self.s.memory is not None and result.text:
            await self.s.memory.record_message(sid, "assistant", result.text)
        await self.s.set_session_status(sid, "idle")
        await self.s.emit(sid, "turn.summary", {"run_id": run_id, **turn.budget.summary(result.status)})
        extra: dict[str, Any] = {"error": result.error} if result.error else {}
        if result.truncated:
            extra["truncated"] = True
        await self.s.emit(
            sid, f"run.{result.status}", {"run_id": run_id, "text": result.text[:4000], **extra}
        )
        return result

    async def _watched(self, rt: SessionRuntime, *args: Any) -> TurnResult:
        """Run the loop as its own task under the liveness watchdog (cancelled → `LIVENESS`)."""
        run_id: str = args[3]
        turn: Turn = args[-1]
        inner = asyncio.create_task(self._loop(rt, *args))
        turn.watchdog.start(inner)
        try:
            return await inner
        except asyncio.CancelledError:
            me = asyncio.current_task()
            if not turn.watchdog.fired or (me is not None and me.cancelling()):
                raise
            msg = f"no stream or tool progress for {turn.watchdog.timeout:g}s"
            return TurnResult(run_id, "aborted", turn.text, {"code": "LIVENESS", "message": msg})
        finally:
            await turn.watchdog.stop()

    async def _loop(
        self,
        rt: SessionRuntime,
        spec: AgentSpec,
        engine: PermissionEngine,
        ctx: ToolContext,
        run_id: str,
        context: str,
        turn_msg: dict[str, Any],
        options: RunOptions,
        turn: Turn,
    ) -> TurnResult:
        sid = rt.session_id
        loop_cfg = self.s.settings.loop
        max_steps = max(1, min(spec.max_steps, loop_cfg.max_iterations, options.max_steps or 10**9))
        error_cap = max(1, min(loop_cfg.outer_loop_errors, max_steps))  # §8.9 outer-loop cap
        budget, guards = turn.budget, LoopGuards(self, rt, run_id)
        gates = StopGates(self, rt, spec.name, run_id)
        open_calls: list[ToolCall] = []
        step = errors = 0
        try:
            while step < max_steps:
                step += 1
                await self._claim_step(rt, run_id, budget.wrapup_note())
                exhausted = budget.exhausted()
                last_step = step == max_steps or exhausted is not None
                note = GRACE_NOTE.format(why=exhausted) if exhausted else None
                note = note or (STEP_LIMIT_NOTE if last_step else None)
                tools = self._available(spec, rt.state, options)
                build = partial(self._request, spec, rt, tools, context, turn_msg, last_step, run_id, note)
                await self.compaction.pre_step(rt, build())  # `build` re-renders after compaction
                await self.s.emit(sid, "step.started", {"run_id": run_id, "step": step, "tools": len(tools)})
                budget.consume_step(grace=exhausted is not None)
                try:
                    out = await model_step(self, rt, run_id, build, budget, turn.watchdog.touch)
                except GatewayError as exc:
                    errors += 1
                    if not exc.retryable or errors >= error_cap:
                        raise
                    await self.s.emit(
                        sid, "run.retry", {"run_id": run_id, "code": exc.code, "errors": errors}
                    )
                    await asyncio.sleep(min(exc.retry_after_ms or 0, 5_000) / 1000)
                    step -= 1
                    continue
                errors = 0
                text, calls = out.text, out.calls
                over_budget = await self.usage.over_budget(sid, self.s.settings.max_cost_usd)
                if last_step or over_budget:
                    calls = []  # forced final step / budget: no further tool calls
                await self._commit_assistant(rt, run_id, step, text, calls)
                turn.text = text or turn.text
                if exhausted is not None:  # that was the one grace call
                    msg = f"run budget exhausted ({exhausted})"
                    return TurnResult(
                        run_id, "blocked", turn.text, {"code": "BUDGET_EXHAUSTED", "message": msg}
                    )
                if over_budget:
                    msg = f"session cost reached max_cost_usd={self.s.settings.max_cost_usd}"
                    return TurnResult(run_id, "blocked", turn.text, {"code": "BUDGET", "message": msg})
                if out.empty and not turn.text:
                    msg = "the model returned an empty response twice"
                    return TurnResult(run_id, "failed", "", {"code": "EMPTY_RESPONSE", "message": msg})
                if not calls:
                    if out.truncated:
                        return TurnResult(run_id, "completed", turn.text, truncated=True)
                    if not last_step and (await gates.check(turn.text) or await self._keep_going(rt, budget)):
                        continue
                    status: TurnStatus = "max_steps" if last_step and max_steps > 1 else "completed"
                    return TurnResult(run_id, status, turn.text)

                open_calls = list(calls)
                by_name = {t.name: t for t in tools}
                turn.watchdog.busy(
                    max((by_name[c.name].manifest.timeout_ms for c in calls if c.name in by_name), default=0)
                    / 1000
                )
                outcomes = await tool_round(self, rt, spec, engine, ctx, run_id, calls, by_name)
                turn.watchdog.busy(0)
                budget.tools += len(outcomes)
                for o in outcomes:
                    open_calls.remove(o.call)
                for msg in rt.state.pending_user_messages:
                    await self._persist(rt, {"role": "user", "content": msg})
                rt.state.pending_user_messages.clear()

                if any(o.halt for o in outcomes):
                    return TurnResult(
                        run_id,
                        "blocked",
                        turn.text,
                        {"code": "PERMISSION_REJECTED", "message": "user rejected a tool call"},
                    )
                if (guard_error := await guards.observe(outcomes)) is not None:
                    return TurnResult(run_id, "blocked", turn.text, guard_error)
                if all_free(outcomes):
                    budget.iterations.refund()
            return TurnResult(run_id, "max_steps", turn.text)
        except GatewayError as exc:
            return TurnResult(run_id, "failed", turn.text, {"code": exc.code, "message": exc.message})
        except CompactionError as exc:  # only raised once the circuit breaker trips
            return TurnResult(run_id, "failed", turn.text, {"code": "COMPACTION", "message": exc.reason})
        except asyncio.CancelledError:
            for call in open_calls:
                await self._persist(
                    rt,
                    {"role": "tool", "tool_call_id": call.id, "content": ABORTED_BEFORE_DISPATCH},
                )
            if not turn.watchdog.fired:  # a liveness cancel is reported by run_turn
                await self.s.set_session_status(sid, "idle")
                await self.s.emit(sid, "run.aborted", {"run_id": run_id})
            raise
        except Exception as exc:  # local bug: no retry, surface it (§8.9)
            log.exception("turn crashed")
            return TurnResult(
                run_id,
                "failed",
                turn.text,
                {"code": "INTERNAL", "message": f"{type(exc).__name__}: {exc}"},
            )

    async def _claim_step(self, rt: SessionRuntime, run_id: str, wrapup: str | None) -> None:
        """Persist claimed steer/inject input (and a due budget wrap-up nudge) before a step."""
        for steer in [*rt.inbox.claim_step(), *([wrapup] if wrapup else [])]:
            await self._persist(rt, {"role": "user", "content": steer})
            data = {"run_id": run_id, "role": "user", "content": steer, "steer": True}
            await self.s.emit(rt.session_id, "message.created", data)

    async def _keep_going(self, rt: SessionRuntime, budget: TurnBudget) -> bool:
        """Token budget below `completion_threshold`: one more push (diminishing-returns aware)."""
        note = budget.continue_note()
        if note is not None:
            await self._persist(rt, {"role": "user", "content": note})
        return note is not None

    async def _commit_assistant(
        self, rt: SessionRuntime, run_id: str, step: int, text: str, calls: list[ToolCall]
    ) -> None:
        assistant: dict[str, Any] = {"role": "assistant", "content": text or None}
        if calls:
            assistant["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for c in calls
            ]
        await self._persist(rt, assistant)
        await self.s.emit(
            rt.session_id,
            "message.completed",
            {
                "run_id": run_id,
                "step": step,
                "content": text,
                "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in calls],
            },
        )

    # ------------------------------------------------------------- helpers
    def _available(
        self, spec: AgentSpec, state: SessionState, options: RunOptions | None = None
    ) -> list[ToolDef]:
        loaded = [t for t in spec.deferred if t.name in state.loaded_tools]
        tools = [*spec.tools, *loaded]
        if options is None or (options.tools_allow is None and not options.tools_deny):
            return tools
        allow = options.tools_allow
        return [
            t
            for t in tools
            if (allow is None or any(fnmatch.fnmatchcase(t.name, g) for g in allow))
            and not any(fnmatch.fnmatchcase(t.name, g) for g in options.tools_deny)
        ]

    def _request(
        self,
        spec: AgentSpec,
        rt: SessionRuntime,
        tools: list[ToolDef],
        context: str,
        turn_msg: dict[str, Any],
        last_step: bool,
        run_id: str,
        note: str | None = None,
    ) -> LLMRequest:
        msgs: list[dict[str, Any]] = [{"role": "system", "content": spec.system_prompt}]
        turn_user_idx = turn_index(rt.history, turn_msg)
        for i, m in enumerate(rt.history):
            if i == turn_user_idx and context:
                m = {**m, "content": f"<context>\n{context}\n</context>\n\n{m['content']}"}
            msgs.append(m)
        if note:
            msgs.append({"role": "user", "content": note})
        msgs = prepare_messages(repair(msgs), self.s.gateway.supports_vision(spec.model))
        variant = spec.variant
        temperature = spec.agent.manifest.temperature
        if temperature is None and variant is not None:
            temperature = variant.temperature
        return LLMRequest(
            model=spec.model,
            messages=tuple(msgs),
            tools=tuple(tool_schema(t.name, t.manifest.description, t.manifest.parameters) for t in tools),
            tool_choice="none" if last_step else "auto",
            temperature=temperature,
            omit_fields=tuple(variant.omit_fields) if variant else (),
            reasoning_effort=self.policy.effort(run_id, spec.agent.manifest.effort),
            metadata={"session_id": rt.session_id, "run_id": run_id},
            variant=variant,
            turn_state=run_id,
        )
