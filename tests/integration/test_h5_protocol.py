"""H5 protocol fixes: `POST /api/events:tail` as SSE with a `[DONE]` sentinel (JSON on
`Accept: application/json`), lifespan teardown order (§24.7), the §12 deliverable rules."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api import teardown as td
from auoreo.api.app import create_app
from auoreo.api.routers.events import wants_json
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.harness.orchestrator import build_spec
from tests.integration.test_api import parse_sse


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            yield c


async def test_events_tail_streams_sse_with_done(client: httpx.AsyncClient, services: Services) -> None:
    for i in range(4):
        await services.emit("server", "custom.t", {"i": i})
    r = await client.post("/api/events:tail", json={"limit": 2})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert r.text.rstrip().endswith("event: done\ndata: [DONE]\nid: " + str(await services.events.last_seq()))
    events = parse_sse(r.text.rsplit("event: done", 1)[0])  # [DONE] is not JSON
    assert [e["data"]["i"] for e in events if e["type"] == "custom.t"] == [2, 3]
    both = await client.post(
        "/api/events:tail", json={"limit": 1}, headers={"Accept": "application/json, text/event-stream"}
    )
    assert both.headers["content-type"].startswith("text/event-stream")  # MCP-style clients get SSE
    js = await client.post("/api/events:tail", json={"limit": 1}, headers={"Accept": "application/json"})
    assert (
        js.headers["content-type"].startswith("application/json") and js.json()["events"][0]["data"]["i"] == 3
    )


def test_wants_json() -> None:
    assert wants_json("application/json") and not wants_json(None) and not wants_json("*/*")
    assert not wants_json("application/json, text/event-stream")


class _Rec:
    def __init__(self, log: list[str], name: str) -> None:
        self.log, self.name = log, name

    async def close(self) -> None:
        self.log.append(f"{self.name}.close")


class _Outbox:
    def __init__(self, log: list[str], busy: int) -> None:
        self.log, self.busy = log, busy

    async def list(self, status: str | None = None, limit: int = 50) -> list[int]:
        self.log.append("outbox.poll")
        self.busy -= 1
        return [1] if self.busy > 0 else []


class _Svc:
    def __init__(self, log: list[str], busy: int = 3) -> None:
        worker = _Rec(log, "deliveries")
        worker.outbox = _Outbox(log, busy)  # type: ignore[attr-defined]
        worker.active = True  # type: ignore[attr-defined]
        self.ext: dict[str, Any] = {"scheduler": _Rec(log, "scheduler"), "deliveries": worker}
        self.log = log
        self.connections = self

    async def close_all(self, code: int) -> None:
        self.log.append(f"ws.close({code})")

    async def close(self) -> None:
        self.log.append("svc.close")


class _App:
    class state:  # noqa: N801
        pass


async def test_teardown_order() -> None:
    log: list[str] = []
    app = _App()
    await td.teardown(app, _Svc(log), owned=True, drain_s=2)
    assert log == [
        "scheduler.close",
        "outbox.poll",
        "outbox.poll",
        "outbox.poll",
        "ws.close(1001)",
        "svc.close",
    ]
    assert app.state.shutting_down is True
    log.clear()
    await td.teardown(_App(), _Svc(log), owned=False)
    assert log == ["ws.close(1001)"]  # borrowed services: only the connections are ours


async def test_drain_times_out() -> None:
    log: list[str] = []
    assert await td.drain_deliveries(_Svc(log, busy=10_000), timeout_s=0.05, poll_s=0.01) is False


async def test_owned_lifespan_uses_teardown(
    settings: Settings, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[bool] = []
    real = td.teardown

    async def spy(app: Any, svc: Any, *, owned: bool, drain_s: float = td.DRAIN_S) -> None:
        seen.append(owned)
        await real(app, svc, owned=owned, drain_s=drain_s)

    monkeypatch.setattr("auoreo.api.app.teardown", spy)
    app = create_app(settings, cwd=workdir)
    async with LifespanManager(app):
        pass
    assert seen == [True] and app.state.shutting_down is True


async def test_deliverable_prompt_rules(services: Services) -> None:
    prompt = build_spec(services.harness, "default", "default", "manual").system_prompt
    assert "under 4 lines" in prompt and "run the project's lint and typecheck" in prompt
    assert "`path:line`" in prompt
