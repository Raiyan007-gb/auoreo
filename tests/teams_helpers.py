"""Test helpers for teams: a fake gateway scripted per session (keyed by prompt text)."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.gateway.types import LLMRequest, StreamItem, ToolCall, text_of
from auoreo.harness.manifests import Rule
from auoreo.harness.orchestrator import build_spec
from auoreo.tools.context import ToolContext


def first_user(request: LLMRequest) -> str:
    for m in request.messages:
        if m.get("role") == "user":
            return text_of(m.get("content"))
    return ""


class RoutedGateway(FakeGateway):
    """Pops replies from the script whose key occurs in the request's FIRST user message
    (the session's first prompt), so concurrent sessions are scripted independently.
    A key with a gate blocks until the gate is set. Unrouted requests use FakeGateway."""

    def __init__(self) -> None:
        super().__init__()
        self.routes: dict[str, deque[Scripted]] = {}
        self.gates: dict[str, asyncio.Event] = {}

    def route(self, key: str, *items: Scripted, gate: asyncio.Event | None = None) -> None:
        self.routes.setdefault(key, deque()).extend(items)
        if gate is not None:
            self.gates[key] = gate

    def requests_for(self, key: str) -> list[LLMRequest]:
        return [
            r
            for r in self.requests
            if key in first_user(r) and r.metadata.get("purpose") not in {"title", "compaction"}
        ]

    async def stream(self, request: LLMRequest) -> AsyncIterator[StreamItem]:
        purpose = request.metadata.get("purpose")
        key = next((k for k in self.routes if k in first_user(request)), None)
        if key is None or purpose in {"title", "compaction", "vision"}:
            async for item in super().stream(request):
                yield item
            return
        self.requests.append(request)
        if key in self.gates:
            await self.gates[key].wait()
        queue = self.routes[key]
        item = queue.popleft() if queue else Scripted(text=f"echo: {key}")
        self.script.appendleft(item)  # reuse FakeGateway's streaming of one Scripted
        self.requests.remove(request)
        async for out in super().stream(request):
            yield out


async def new_parent(svc: Services, agent: str = "default", title: str = "parent") -> str:
    row = await svc.create_session(title=title, agent=agent)
    return row.id


async def turn(svc: Services, sid: str, text: str, timeout: float = 20) -> Any:
    run_id = await svc.coordinator.submit(sid, text, interactive=False)
    await svc.coordinator.wait(sid, timeout)
    return svc.coordinator.result(run_id or "")


def tool_ctx(svc: Services, session_id: str, agent: str = "default") -> ToolContext:
    spec = build_spec(svc.harness, agent, "default", "manual")
    engine = PermissionEngine([Rule(permission="*", pattern="*", action="allow")])
    return ToolContext(
        session_id=session_id,
        cwd=svc.cwd,
        home=svc.settings.home,
        harness=svc.harness,
        services={"memory": svc.memory, "services": svc, "engine": engine, "spec": spec},
    )


async def call_tool(svc: Services, ctx: ToolContext, name: str, args: dict[str, Any]) -> tuple[str, bool]:
    out = await svc.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)),
        tools=svc.harness.tools,
        ctx=ctx,
        engine=ctx.services["engine"],
    )
    return out.result.content, out.result.is_error


def write_agent(root: Path, name: str, extra: str = "") -> None:
    d = root / ".auoreo" / "agents" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "AGENT.md").write_text(
        f"---\nname: {name}\ndescription: test agent {name}\n{extra}---\nYou are {name}.\n", encoding="utf-8"
    )
