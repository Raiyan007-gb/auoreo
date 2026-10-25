"""Agent `effort` → `LLMRequest.reasoning_effort` on main turns only (W1-C)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from auoreo.core.services import Services
from auoreo.gateway.fake import FakeGateway, Scripted
from tests.conftest import write

THINKER = """---
name: thinker
description: Thinks hard.
tools:
  allow: ["*"]
effort: high
---
Think before answering.
"""


async def _turn(svc: Services, text: str, agent: str | None = None) -> str:
    sess = await svc.create_session(agent=agent) if agent else await svc.create_session()
    rid = await svc.coordinator.submit(sess.id, text, interactive=False)
    await svc.coordinator.wait(sess.id, 10)
    result = svc.coordinator.result(rid or "")
    assert result is not None
    return result.status


def _aux(gateway: FakeGateway) -> list[Any]:
    return [r for r in gateway.requests if "purpose" in r.metadata]


async def test_default_agent_sends_no_effort(services: Services, gateway: FakeGateway) -> None:
    assert await _turn(services, "hello") == "completed"
    assert gateway.efforts == [None]
    assert all(r.reasoning_effort is None for r in gateway.requests)


async def test_agent_effort_is_sent_on_every_main_step_but_never_on_aux_calls(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    write(workdir / ".auoreo" / "agents" / "thinker" / "AGENT.md", THINKER)
    services.harness.reload()
    (workdir / "a.txt").write_text("A")
    gateway.queue(Scripted(tool_calls=[("read", {"path": "a.txt"})]), Scripted(text="done"))
    assert await _turn(services, "read it", agent="thinker") == "completed"
    assert gateway.efforts == ["high", "high"]
    assert all(r.reasoning_effort is None for r in _aux(gateway))


async def test_compaction_aux_call_never_carries_effort(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    write(workdir / ".auoreo" / "agents" / "thinker" / "AGENT.md", THINKER)
    services.harness.reload()
    sess = await services.create_session(agent="thinker")
    for i in range(15):
        await services.coordinator.submit(sess.id, f"turn {i} " + "x" * 400, interactive=False)
        await services.coordinator.wait(sess.id, 10)
    await services.coordinator.runner.compaction.compact(sess.id, trigger="manual")
    purposes = {r.metadata.get("purpose") for r in _aux(gateway)}
    assert "compaction" in purposes
    assert all(r.reasoning_effort is None for r in _aux(gateway))


@pytest.mark.parametrize(("effort", "sent"), [("low", "low"), ("high", "high"), ("max", "high")])
def test_litellm_gateway_maps_effort(effort: str, sent: str) -> None:
    from auoreo.gateway.gateway import effort_param

    assert effort_param(effort) == sent


async def test_litellm_gateway_passes_effort_only_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from auoreo.core.settings import Settings
    from auoreo.gateway.gateway import LiteLLMGateway
    from auoreo.gateway.types import LLMRequest

    gw = LiteLLMGateway(Settings(gateway="litellm"), lambda _n: None)
    seen: list[dict[str, Any]] = []

    async def fake_open(kwargs: dict[str, Any]) -> Any:
        seen.append(kwargs)
        raise RuntimeError("stop")

    monkeypatch.setattr(gw, "_open", fake_open)
    for effort in (None, "max"):
        req = LLMRequest(
            model="default", messages=({"role": "user", "content": "x"},), reasoning_effort=effort
        )  # type: ignore[arg-type]
        with pytest.raises(RuntimeError):
            async for _ in gw.stream(req):
                pass
    assert "reasoning_effort" not in seen[0]
    assert seen[1]["reasoning_effort"] == "high"
