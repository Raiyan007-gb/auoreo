"""SessionRunCoordinator: one drain task per session; wakes coalesce (spec §8.1).

It also runs workflows: a one-step workflow runs inside the session; multi-step
workflows run each step in a child session and thread outputs through templates.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import TYPE_CHECKING, Any

from auoreo.core.permissions import PermissionEngine
from auoreo.harness.manifests import WorkflowManifest
from auoreo.harness.orchestrator import render
from auoreo.runtime.inbox import InputKind, TurnRequest
from auoreo.runtime.runner import AgentRunner, SessionRuntime, TurnResult

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)


class SessionRunCoordinator:
    def __init__(self, services: Services) -> None:
        self.s = services
        self.runner = AgentRunner(services)
        self._runtimes: dict[str, SessionRuntime] = {}
        self._results: dict[str, TurnResult] = {}
        self._idle: dict[str, asyncio.Event] = {}

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
        )
        self._runtimes[session_id] = rt
        return rt

    def engine_for(self, session_id: str) -> PermissionEngine | None:
        rt = self._runtimes.get(session_id)
        return rt.engine if rt else None

    def is_running(self, session_id: str) -> bool:
        rt = self._runtimes.get(session_id)
        return bool(rt and rt.running)

    async def submit(
        self,
        session_id: str,
        text: str,
        kind: InputKind = "followup",
        *,
        agent: str | None = None,
        workflow: str | None = None,
        model: str | None = None,
        interactive: bool = True,
    ) -> str | None:
        """Queue input. Returns the run id for followups (None for steer/inject)."""
        rt = await self.runtime(session_id)
        rt.interactive = interactive
        if kind == "inject":
            rt.inbox.inject(text)
            return None
        if kind == "steer" and rt.running:
            rt.inbox.steer(text)
            return None
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        rt.inbox.followup(TurnRequest(run_id, text, agent, workflow, model))
        self._idle.setdefault(session_id, asyncio.Event()).clear()
        if rt.task is None or rt.task.done():
            rt.task = asyncio.create_task(self._drain(rt), name=f"drain:{session_id}")
        return run_id

    async def _drain(self, rt: SessionRuntime) -> None:
        try:
            while (req := rt.inbox.claim_turn()) is not None:
                rt.running = True
                try:
                    result = await self._run_request(rt, req)
                except asyncio.CancelledError:
                    rt.inbox.next_turn.clear()
                    raise
                except Exception:
                    log.exception("run %s crashed", req.run_id)
                    result = TurnResult(req.run_id, "failed", "", {"code": "INTERNAL"})
                self._results[req.run_id] = result
        finally:
            rt.running = False
            self._idle.setdefault(rt.session_id, asyncio.Event()).set()

    async def _run_request(self, rt: SessionRuntime, req: TurnRequest) -> TurnResult:
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
            text = render(step.input, {"input": req.text, "steps": {}})
            # The session's own agent wins for its own workflow; otherwise the step's agent.
            agent = req.agent or (rt.agent if wf_name == rt.workflow else step.agent)
            return await self.runner.run_turn(rt, text, agent=agent, model=req.model, run_id=req.run_id)
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
            )
            crt = await self.runtime(child.id)
            crt.interactive = rt.interactive
            text = render(step.input, {"input": req.text, "steps": outputs})
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

    async def cancel(self, session_id: str) -> bool:
        rt = self._runtimes.get(session_id)
        if rt is None or rt.task is None or rt.task.done():
            return False
        rt.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await rt.task
        return True

    async def shutdown(self) -> None:
        for sid in list(self._runtimes):
            await self.cancel(sid)
