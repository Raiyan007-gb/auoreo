"""SessionRunCoordinator: one drain task per session; wakes coalesce (spec §8.1).

It also runs workflows: a one-step workflow runs inside the session; multi-step
workflows run each step in a child session and thread outputs through templates.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Coroutine
from functools import partial
from typing import TYPE_CHECKING, Any

from auoreo.core.permissions import PermissionEngine
from auoreo.harness.manifests import WorkflowManifest
from auoreo.memory.scrubber import sanitize_context
from auoreo.runtime import commands
from auoreo.runtime import cwd as session_cwd
from auoreo.runtime.compaction_idle import IdleCompactor
from auoreo.runtime.hooks import hooks_of
from auoreo.runtime.inbox import InboxFull, InputKind, RunOptions, TurnRequest
from auoreo.runtime.runner import AgentRunner, SessionRuntime, TurnResult
from auoreo.runtime.session_memory import SessionMemoryWriter
from auoreo.runtime.titles import generate_title
from auoreo.runtime.workflow_loop import run_looping, step_input
from auoreo.tools.context import restore_state

if TYPE_CHECKING:
    from pathlib import Path

    from auoreo.core.services import Services

log = logging.getLogger(__name__)


class SessionBusy(Exception):
    """A manual compaction was asked for while a turn runs (spec §11.9 S4(d): requires idle)."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"session '{session_id}' is busy; compact when it is idle")
        self.session_id = session_id


class SessionRunCoordinator:
    def __init__(self, services: Services) -> None:
        self.s = services
        self.runner = AgentRunner(services)
        self._runtimes: dict[str, SessionRuntime] = {}
        self._results: dict[str, TurnResult] = {}
        self._idle: dict[str, asyncio.Event] = {}
        self._background: set[asyncio.Task[Any]] = set()
        self._compacting: set[str] = set()  # --- I4: manual compaction holds the start lock
        self.idle = IdleCompactor(services, self.runner.compaction)  # --- G2b: S4(c)
        self.notes = SessionMemoryWriter(services)  # --- I4: S7 session-memory notes

    async def runtime(self, session_id: str) -> SessionRuntime:
        rt = self._runtimes.get(session_id)
        if rt is not None:
            return rt
        row = await self.s.get_session(session_id)
        if row is None:
            raise KeyError(f"unknown session '{session_id}'")
        rt = SessionRuntime(
            session_id,
            row.agent,
            row.workflow,
            row.model,
            history=await self.runner.load_history(session_id),
            subagent=row.parent_id is not None,
        )
        restore_state(rt.state, rt.history)  # tool_search tools, plan, verify verdict, plan mode
        await session_cwd.load(self.s, rt)  # --- M7 W4: validated per-session cwd (flag off: no-op)
        self._runtimes[session_id] = rt
        return rt

    def cwd_for(self, session_id: str) -> Path:
        """The workspace root of a loaded session (the server cwd for any other id)."""
        return session_cwd.cwd_of(self._runtimes.get(session_id), self.s)

    def engine_for(self, session_id: str) -> PermissionEngine | None:
        rt = self._runtimes.get(session_id)
        return rt.engine if rt else None

    def history_of(self, session_id: str) -> list[dict[str, Any]] | None:
        """The live message list of a loaded session (the list object the runner sends from)."""
        rt = self._runtimes.get(session_id)
        return rt.history if rt else None

    def is_running(self, session_id: str) -> bool:
        rt = self._runtimes.get(session_id)
        return bool(rt and rt.running)

    def is_busy(self, session_id: str) -> bool:
        """Running a turn, draining queued input, or inside a manual compaction."""
        rt = self._runtimes.get(session_id)
        draining = bool(rt and rt.task is not None and not rt.task.done())
        return self.is_running(session_id) or draining or session_id in self._compacting

    @contextlib.asynccontextmanager
    async def compaction_lock(self, session_id: str) -> AsyncIterator[SessionRuntime]:
        """Hold the session's start lock for a whole manual compaction (S4(d)/S6): refused
        (`SessionBusy`) while a turn runs; input that arrives meanwhile is queued and its turn
        starts only after the replace transaction has committed."""
        rt = await self.runtime(session_id)
        if self.is_busy(session_id):
            raise SessionBusy(session_id)
        await self.idle.preempt(session_id)
        self.idle.cancel_timer(session_id)
        self._compacting.add(session_id)
        try:
            yield rt
        finally:
            self._compacting.discard(session_id)
            if rt.inbox.next_turn and (rt.task is None or rt.task.done()):
                rt.task = asyncio.create_task(self._drain(rt), name=f"drain:{session_id}")

    async def submit(
        self,
        session_id: str,
        text: str,
        kind: InputKind = "followup",
        *,
        agent: str | None = None,
        workflow: str | None = None,
        model: str | None = None,
        interactive: bool | None = None,
        options: RunOptions | None = None,
    ) -> str | None:
        """Queue input. Returns the run id for followups (None for steer/inject and for
        engine-handled slash commands such as `/model`). `interactive` is only changed
        when given explicitly: injected mail or steering never flips a session's mode."""
        rt = await self.runtime(session_id)
        text = sanitize_context(text)  # --- I4 §11.7: strip leaked <memory-context> from input
        await self.idle.preempt(session_id)  # foreground input beats a background compaction
        if interactive is not None:
            rt.interactive = interactive
        if kind == "followup" or (kind == "steer" and not rt.running):
            expanded = await commands.expand(self, rt, text, agent)
            if expanded.handled:
                return None
            text, agent = expanded.text, expanded.agent
        if kind == "inject":
            rt.inbox.inject(text)
            return None
        if kind == "steer" and rt.running:
            rt.inbox.steer(text)
            return None
        bound = self.s.settings.http.queue_bound
        if rt.inbox.full(bound):  # --- G1a: bounded inbox (429 at the API edge)
            raise InboxFull(session_id, bound, self.s.settings.http.queue_retry_after_s)
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        rt.inbox.followup(TurnRequest(run_id, text, agent, workflow, model, options))
        self._idle.setdefault(session_id, asyncio.Event()).clear()
        if (rt.task is None or rt.task.done()) and session_id not in self._compacting:
            rt.task = asyncio.create_task(self._drain(rt), name=f"drain:{session_id}")
        return run_id

    async def _drain(self, rt: SessionRuntime) -> None:
        try:
            while (req := rt.inbox.claim_turn()) is not None:
                rt.running = True
                row = await self.s.get_session(rt.session_id)
                needs_title = row is not None and not row.title
                try:
                    result = await self._run_request(rt, req)
                except asyncio.CancelledError:
                    rt.inbox.cancel("user", keep_inbox=rt.keep_inbox_on_cancel)
                    raise
                except Exception:
                    log.exception("run %s crashed", req.run_id)
                    result = TurnResult(req.run_id, "failed", "", {"code": "INTERNAL"})
                self._results[req.run_id] = result
                await self.notes.record(rt, req.text, result)
                if needs_title and result.status in {"completed", "max_steps"}:
                    self._spawn(generate_title(self.s, rt.session_id, req.text, result.text))
        finally:
            rt.running = False
            self._idle.setdefault(rt.session_id, asyncio.Event()).set()
            self.idle.schedule(rt)

    async def _session_start(self, rt: SessionRuntime, agent: str) -> None:
        """SessionStart hooks, once per runtime (its first turn); best effort."""
        hooks = hooks_of(self.s)
        if hooks is None or rt.session_started:
            return
        rt.session_started = True
        hooks.remember_agent(rt.session_id, agent)
        try:
            await hooks.run(
                "SessionStart",
                session_id=rt.session_id,
                payload={"agent": agent, "workflow": rt.workflow, "cwd": str(session_cwd.cwd_of(rt, self.s))},
                match_key=agent,
            )
        except Exception:
            log.exception("SessionStart hooks failed")

    async def _run_request(self, rt: SessionRuntime, req: TurnRequest) -> TurnResult:
        if rt.cwd_error:  # --- M7 W4: fail closed, never run in the parent checkout instead
            error = {"code": "CONFIG", "message": rt.cwd_error}
            await self.s.emit(rt.session_id, "run.failed", {"run_id": req.run_id, **error})
            return TurnResult(req.run_id, "failed", "", error)
        await self._session_start(rt, req.agent or rt.agent)
        wf_name = req.workflow or rt.workflow
        wf = self.s.harness.workflows.get(wf_name)
        if wf is None:
            msg = f"unknown workflow '{wf_name}'"
            await self.s.emit(
                rt.session_id,
                "run.failed",
                {"run_id": req.run_id, "code": "CONFIG", "message": msg},
            )
            return TurnResult(req.run_id, "failed", "", {"code": "CONFIG", "message": msg})
        manifest: WorkflowManifest = wf.manifest
        if len(manifest.steps) == 1:
            step = manifest.steps[0]
            # The session's own agent wins for its own workflow; otherwise the step's agent.
            agent = req.agent or (rt.agent if wf_name == rt.workflow else step.agent)

            run_ids = [req.run_id]  # the first iteration carries the request's run id

            async def turn(text: str) -> TurnResult:
                run_id = run_ids.pop() if run_ids else None
                return await self.runner.run_turn(
                    rt, text, agent=agent, model=req.model, run_id=run_id, options=req.options
                )

            if step.loop is not None:  # --- I4: ralph-lite loop inside the session
                emit = partial(self.s.emit, rt.session_id)
                return await run_looping(step, req.text, {}, turn, emit, session_cwd.cwd_of(rt, self.s))
            return await turn(step_input(step, req.text, {}))
        return await self._run_workflow(rt, req, manifest)

    async def _run_workflow(self, rt: SessionRuntime, req: TurnRequest, wf: WorkflowManifest) -> TurnResult:
        sid = rt.session_id
        outputs: dict[str, Any] = {}
        await self.s.emit(
            sid,
            "workflow.started",
            {"run_id": req.run_id, "workflow": wf.name, "steps": [s.id for s in wf.steps]},
        )
        await self.runner._persist(rt, {"role": "user", "content": req.text})
        result = TurnResult(req.run_id, "completed", "")
        for step in wf.steps:
            child = await self.s.create_session(
                title=f"{wf.name}:{step.id}",
                agent=step.agent,
                workflow="chat",
                model=req.model,
                parent_id=sid,
                cwd=rt.cwd,  # M7 W4: workflow steps stay in the session's workspace root
            )
            crt = await self.runtime(child.id)
            crt.interactive = rt.interactive
            text = step_input(step, req.text, outputs)
            await self.s.emit(
                sid,
                "workflow.step.started",
                {
                    "run_id": req.run_id,
                    "step": step.id,
                    "agent": step.agent,
                    "child_session": child.id,
                },
            )
            if step.loop is not None:  # --- I4: ralph-lite loop in the step's child session
                result = await run_looping(
                    step,
                    req.text,
                    outputs,
                    partial(self.runner.run_turn, crt, agent=step.agent, model=req.model),
                    partial(self.s.emit, sid),
                    session_cwd.cwd_of(crt, self.s),
                )
            else:
                result = await self.runner.run_turn(crt, text, agent=step.agent, model=req.model)
            outputs[step.id] = {"output": result.text, "status": result.status}
            await self.s.emit(
                sid,
                "workflow.step.completed",
                {
                    "run_id": req.run_id,
                    "step": step.id,
                    "status": result.status,
                    "output": result.text[:4000],
                },
            )
            if result.status not in {"completed", "max_steps"}:
                break
        await self.runner._persist(rt, {"role": "assistant", "content": result.text})
        status = "completed" if result.status in {"completed", "max_steps"} else result.status
        await self.s.emit(
            sid,
            f"run.{status}",
            {"run_id": req.run_id, "text": result.text[:4000], "workflow": wf.name},
        )
        return TurnResult(req.run_id, status, result.text, result.error)

    async def wait(self, session_id: str, timeout: float | None = None) -> None:
        ev = self._idle.setdefault(session_id, asyncio.Event())
        rt = self._runtimes.get(session_id)
        if rt is None or (not rt.running and not rt.inbox.next_turn and (rt.task is None or rt.task.done())):
            return
        await asyncio.wait_for(ev.wait(), timeout)

    def result(self, run_id: str) -> TurnResult | None:
        return self._results.get(run_id)

    async def cancel(self, session_id: str, keep_inbox: bool = True) -> bool:
        """Cancel the running turn. With `keep_inbox` (the default for user cancels) pending
        steer/inject input moves to the next turn and queued followups stay queued (they
        run on the next submit); otherwise they are dropped."""
        rt = self._runtimes.get(session_id)
        if rt is None or rt.task is None or rt.task.done():
            return False
        rt.keep_inbox_on_cancel = keep_inbox
        rt.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await rt.task
        return True

    def forget(self, session_id: str) -> None:
        """Drop cached runtime state for a deleted session."""
        self._runtimes.pop(session_id, None)
        self._idle.pop(session_id, None)
        self.idle.cancel_timer(session_id)

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def background_idle(self, timeout: float | None = None) -> None:
        """Wait for background work (title generation) to finish — tests and shutdown."""
        if self._background:
            await asyncio.wait(list(self._background), timeout=timeout)

    async def shutdown(self) -> None:
        for sid in list(self._runtimes):
            await self.cancel(sid, keep_inbox=False)
        await self.idle.shutdown()
        for task in list(self._background):
            task.cancel()
        await self.background_idle(timeout=2)
