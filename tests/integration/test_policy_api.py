"""`/api/policy/bandit` + `auoreo policy bandit status|reset` (W1-C)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager
from typer.testing import CliRunner

from auoreo.api.app import create_app
from auoreo.cli.app import app as cli
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.policy.config import Arm, BanditSettings


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c


async def test_status_and_reset(client: httpx.AsyncClient, services: Services) -> None:
    body = (await client.get("/api/policy/bandit")).json()
    assert body["enabled"] is False and body["epsilon"] == 0.15 and body["contexts_mode"] == "agent"
    assert [a["key"] for a in body["arms"]][:2] == ["default|-|-", "default|high|-"]
    assert body["stats"] == [] and body["recent"] == []

    services.settings.bandit = BanditSettings(
        enabled=True, arms=[Arm(model="small"), Arm(model="default", effort="high")]
    )
    sess = await services.create_session()
    for text in ("a", "b", "c"):
        await services.coordinator.submit(sess.id, text, interactive=False)
        await services.coordinator.wait(sess.id, 10)
    body = (await client.get("/api/policy/bandit")).json()
    assert body["enabled"] is True
    assert body["contexts"] == [{"context_key": "agent:default", "pulls": 3, "epsilon": 0.15}]
    assert sum(s["pulls"] for s in body["stats"]) == 3 and len(body["recent"]) == 3
    assert {"success", "latency_ms", "norm_cost", "fallbacks"} <= set(body["recent"][0]["signals"])

    r = await client.post("/api/policy/bandit/reset", json={"context": "agent:nope"})
    assert r.json() == {"arms": 0, "outcomes": 0}
    r = await client.post("/api/policy/bandit/reset", json={})
    assert r.status_code == 200 and r.json()["outcomes"] == 3
    assert (await client.get("/api/policy/bandit")).json()["stats"] == []


async def test_policy_choice_event_is_on_the_session_stream(
    client: httpx.AsyncClient, services: Services
) -> None:
    services.settings.bandit = BanditSettings(enabled=True, arms=[Arm(model="default", effort="low")])
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "hi", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    events = await services.events.replay(0, frozenset({f"session:{sess.id}"}))
    choice = next(e for e in events if e.type == "policy.choice")
    assert choice.aggregate_id == sess.id
    assert choice.data["arm"] == {"key": "default|low|-", "model": "default", "effort": "low", "top_k": None}
    assert choice.data["why"].startswith("exploit: untried arm")


def test_cli_status_and_reset(home: Path) -> None:
    (home / "settings.json").write_text(json.dumps({"gateway": "fake", "bandit": {"enabled": True}}))
    runner = CliRunner()
    r = runner.invoke(cli, ["policy", "bandit", "status", "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["enabled"] is True and len(data["arms"]) == 4
    r = runner.invoke(cli, ["policy", "bandit", "status"])
    assert r.exit_code == 0 and "bandit: enabled" in r.output
    r = runner.invoke(cli, ["policy", "bandit", "reset", "--context", "agent:default"])
    assert r.exit_code == 0 and "0 arm rows, 0 outcomes removed" in r.output
