"""The agent loop: one turn = steps of (model call → tool round) until a final answer.

Spec §8: freeze the request per step, persist the user batch before streaming, run
concurrency-safe tool calls in parallel and others as barriers, commit results in model
order, stop on doom loops, and force a tool-free final step when the budget runs out.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import func, select

from auoreo.core.db import MessageRow
from auoreo.core.permissions import PermissionEngine
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.types import Finish, LLMRequest, TextDelta, ToolCall, Usage, tool_schema
from auoreo.harness.loader import ToolDef
from auoreo.harness.orchestrator import AgentSpec, build_spec, floors, render_context
from auoreo.runtime.inbox import Inbox
from auoreo.tools.context import SessionState, ToolContext, ToolResult
from auoreo.tools.executor import ToolExecutor, ToolOutcome

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)

TurnStatus = Literal["completed", "blocked", "aborted", "failed", "max_steps"]
ABORTED_BEFORE_DISPATCH = "[aborted before dispatch]"
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


@dataclass
class TurnResult:
    run_id: str
    status: TurnStatus
    text: str
    error: dict[str, Any] | None = None


class AgentRunner:
    def __init__(self, services: Services) -> None:
        self.s = services
        self.executor = ToolExecutor(services)

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
    def _spec(self, rt: SessionRuntime, agent: str, model_override: str | None) -> AgentSpec:
        from auoreo.gateway.resolve import resolve_model

        manifest = self.s.harness.agent(agent).manifest
        model = resolve_model(
            self.s.settings, session_override=model_override or rt.model, agent_model=manifest.model
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
    ) -> TurnResult:
        run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
        sid = rt.session_id
        agent = agent or rt.agent
        try:
            spec = self._spec(rt, agent, model)
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
        await self.s.set_session_status(sid, "running", title=text)

        user_msg = {"role": "user", "content": text}
        await self._persist(rt, user_msg)
        await self.s.emit(sid, "message.created", {"run_id": run_id, "role": "user", "content": text})
        turn_user_idx = len(rt.history) - 1
        if self.s.memory is not None:
            await self.s.memory.record_message(sid, "user", text)

        memory_block = ""
        if self.s.memory is not None and spec.agent.manifest.memory:
            try:
                memory_block = await self.s.memory.context_block(text)
            except Exception:  # recall must never fail the turn
                log.exception("memory recall failed")
        context = render_context(spec, self.s.cwd, engine.mode, memory_block)

        async def question(qs: list[dict[str, Any]]) -> dict[str, Any]:
            return await self.s.questions.ask(sid, qs)

        async def emit(event_type: str, data: dict[str, Any]) -> None:
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
        result = await self._loop(rt, spec, engine, ctx, run_id, context, turn_user_idx)
        if self.s.memory is not None and result.text:
            await self.s.memory.record_message(sid, "assistant", result.text)
        await self.s.set_session_status(sid, "idle")
        await self.s.emit(
            sid,
            f"run.{result.status}",
            {
                "run_id": run_id,
                "text": result.text[:4000],
                **({"error": result.error} if result.error else {}),
            },
        )
        return result

    async def _loop(
        self,
        rt: SessionRuntime,
        spec: AgentSpec,
        engine: PermissionEngine,
        ctx: ToolContext,
        run_id: str,
        context: str,
        turn_user_idx: int,
    ) -> TurnResult:
        sid = rt.session_id
        loop_cfg = self.s.settings.loop
        max_steps = max(1, min(spec.max_steps, loop_cfg.max_iterations))
        recent: deque[str] = deque(maxlen=max(2, loop_cfg.doom_loop_repeats))
        final_text = ""
        open_calls: list[ToolCall] = []
        try:
            for step in range(1, max_steps + 1):
                for steer in rt.inbox.claim_step():
                    await self._persist(rt, {"role": "user", "content": steer})
                    await self.s.emit(
                        sid,
                        "message.created",
                        {"run_id": run_id, "role": "user", "content": steer, "steer": True},
                    )
                last_step = step == max_steps
                tools = self._available(spec, rt.state)
                request = self._request(spec, rt, tools, context, turn_user_idx, last_step, run_id)
                await self.s.emit(
                    sid,
                    "step.started",
                    {"run_id": run_id, "step": step, "tools": len(request.tools)},
                )
                text, calls = await self._stream(sid, run_id, request)
                if last_step:
                    calls = []  # tool calls on the forced final step are ignored
                assistant: dict[str, Any] = {"role": "assistant", "content": text or None}
                if calls:
                    assistant["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": c.arguments},
                        }
                        for c in calls
                    ]
                await self._persist(rt, assistant)
                await self.s.emit(
                    sid,
                    "message.completed",
                    {
                        "run_id": run_id,
                        "step": step,
                        "content": text,
                        "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in calls],
                    },
                )
                final_text = text or final_text
                if not calls:
                    status: TurnStatus = "max_steps" if last_step and max_steps > 1 else "completed"
                    return TurnResult(run_id, status, final_text)

                open_calls = list(calls)
                outcomes = await self._tool_round(
                    rt, spec, engine, ctx, run_id, calls, {t.name: t for t in tools}
                )
                for o in outcomes:
                    open_calls.remove(o.call)
                for msg in rt.state.pending_user_messages:
                    await self._persist(rt, {"role": "user", "content": msg})
                rt.state.pending_user_messages.clear()

                if any(o.halt for o in outcomes):
                    return TurnResult(
                        run_id,
                        "blocked",
                        final_text,
                        {"code": "PERMISSION_REJECTED", "message": "user rejected a tool call"},
                    )
                for o in outcomes:
                    recent.append(_signature(o))
                if len(recent) == recent.maxlen and len(set(recent)) == 1:
                    return TurnResult(
                        run_id,
                        "blocked",
                        final_text,
                        {
                            "code": "DOOM_LOOP",
                            "message": f"identical tool call repeated {recent.maxlen}x with the same result",
                        },
                    )
            return TurnResult(run_id, "max_steps", final_text)
        except GatewayError as exc:
            return TurnResult(run_id, "failed", final_text, {"code": exc.code, "message": exc.message})
        except asyncio.CancelledError:
            for call in open_calls:
                await self._persist(
                    rt,
                    {"role": "tool", "tool_call_id": call.id, "content": ABORTED_BEFORE_DISPATCH},
                )
            await self.s.set_session_status(sid, "idle")
            await self.s.emit(sid, "run.aborted", {"run_id": run_id})
            raise
        except Exception as exc:  # local bug: no retry, surface it (§8.9)
            log.exception("turn crashed")
            return TurnResult(
                run_id,
                "failed",
                final_text,
                {"code": "INTERNAL", "message": f"{type(exc).__name__}: {exc}"},
            )

    # ------------------------------------------------------------- helpers
    def _available(self, spec: AgentSpec, state: SessionState) -> list[ToolDef]:
        loaded = [t for t in spec.deferred if t.name in state.loaded_tools]
        return [*spec.tools, *loaded]

    def _request(
        self,
        spec: AgentSpec,
        rt: SessionRuntime,
        tools: list[ToolDef],
        context: str,
        turn_user_idx: int,
        last_step: bool,
        run_id: str,
    ) -> LLMRequest:
        msgs: list[dict[str, Any]] = [{"role": "system", "content": spec.system_prompt}]
        for i, m in enumerate(rt.history):
            if i == turn_user_idx and context:
                m = {**m, "content": f"<context>\n{context}\n</context>\n\n{m['content']}"}
            msgs.append(m)
        if last_step:
            msgs.append({"role": "user", "content": STEP_LIMIT_NOTE})
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
            metadata={"session_id": rt.session_id, "run_id": run_id},
        )

    async def _stream(self, sid: str, run_id: str, request: LLMRequest) -> tuple[str, list[ToolCall]]:
        parts: list[str] = []
        calls: list[ToolCall] = []
        async for item in self.s.gateway.stream(request):
            match item:
                case TextDelta(text=t):
                    parts.append(t)
                    await self.s.emit(sid, "message.delta", {"run_id": run_id, "text": t})
                case ToolCall():
                    calls.append(item)
                case Usage():
                    await self.s.emit(
                        sid,
                        "usage",
                        {
                            "run_id": run_id,
                            "model": request.model,
                            "prompt_tokens": item.prompt_tokens,
                            "completion_tokens": item.completion_tokens,
                            "cost_usd": item.cost_usd,
                        },
                    )
                case Finish():
                    pass
        return "".join(parts), calls

    async def _tool_round(
        self,
        rt: SessionRuntime,
        spec: AgentSpec,
        engine: PermissionEngine,
        ctx: ToolContext,
        run_id: str,
        calls: list[ToolCall],
        tools: dict[str, ToolDef],
    ) -> list[ToolOutcome]:
        """Consecutive concurrency-safe calls run together; anything else is a barrier.
        Results are committed strictly in model order."""
        sem = asyncio.Semaphore(max(1, self.s.settings.loop.max_parallel_tool_calls))
        outcomes: list[ToolOutcome] = []
        batches: list[list[ToolCall]] = []
        for call in calls:
            t = tools.get(call.name)
            safe = bool(t and t.manifest.is_concurrency_safe)
            if safe and batches and batches[-1] and _all_safe(batches[-1], tools):
                batches[-1].append(call)
            else:
                batches.append([call])

        async def one(call: ToolCall) -> ToolOutcome:
            async with sem:
                await self.s.emit(
                    rt.session_id,
                    "tool.started",
                    {
                        "run_id": run_id,
                        "call_id": call.id,
                        "tool": call.name,
                        "arguments": call.arguments[:4000],
                    },
                )
                return await self.executor.run(call, tools=tools, ctx=ctx, engine=engine)

        for batch in batches:
            results = await asyncio.gather(*(one(c) for c in batch))
            for o in results:
                await self._persist(
                    rt, {"role": "tool", "tool_call_id": o.call.id, "content": o.result.content}
                )
                await self.s.emit(
                    rt.session_id,
                    "tool.completed",
                    {
                        "run_id": run_id,
                        "call_id": o.call.id,
                        "tool": o.call.name,
                        "is_error": o.result.is_error,
                        "content": o.result.content[:4000],
                        "meta": o.result.meta,
                    },
                )
                outcomes.append(o)
            if any(o.halt for o in results):
                break  # do not start later calls after a rejection
        for call in calls[len(outcomes) :]:  # unstarted calls after a halt
            await self._persist(
                rt, {"role": "tool", "tool_call_id": call.id, "content": ABORTED_BEFORE_DISPATCH}
            )
            outcomes.append(ToolOutcome(call, ToolResult(ABORTED_BEFORE_DISPATCH, is_error=True)))
        return outcomes


def _all_safe(batch: list[ToolCall], tools: dict[str, ToolDef]) -> bool:
    return all((t := tools.get(c.name)) is not None and t.manifest.is_concurrency_safe for c in batch)


def _signature(o: ToolOutcome) -> str:
    try:
        args = json.dumps(json.loads(o.call.arguments or "{}"), sort_keys=True)
    except json.JSONDecodeError:
        args = o.call.arguments
    digest = hashlib.sha256(o.result.content.encode()).hexdigest()[:16]
    return f"{o.call.name}|{args}|{digest}"
