from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager
from typer.testing import CliRunner

from auoreo.api.app import create_app
from auoreo.cli.app import app as cli
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.core.usage import UsageLedger
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.gateway.types import Usage


async def test_ledger_estimates_missing_cost_and_summarizes(services: Services) -> None:
    ledger = UsageLedger(services.db, lambda model, p, c: (p + 2 * c) / 1e6)
    await ledger.record("s1", "r1", "m-a", Usage(1000, 100))  # no cost → estimated
    await ledger.record("s1", "r2", "m-a", Usage(500, 50, cost_usd=0.5))  # reported cost
    await ledger.record("s2", "r3", "m-b", Usage(10, 1, cost_usd=None, estimated=True))
    rows = await ledger.by_session("s1")
    assert [r.run_id for r in rows] == ["r2", "r1"]
    assert rows[1].estimated and rows[1].cost_usd == pytest.approx(0.0012)
    assert not rows[0].estimated and rows[0].cost_usd == 0.5
    assert await ledger.session_cost("s1") == pytest.approx(0.5012)
    summary = await ledger.summary(7)
    assert summary["total"]["calls"] == 3 and summary["total"]["prompt_tokens"] == 1510
    by_model = {r["key"]: r for r in summary["by_model"]}
    assert by_model["m-a"]["calls"] == 2 and by_model["m-b"]["completion_tokens"] == 1
    assert {r["key"] for r in summary["by_session"]} == {"s1", "s2"}
    assert await ledger.over_budget("s1", 0.5) and not await ledger.over_budget("s1", None)


async def test_ledger_survives_a_broken_price_table(services: Services) -> None:
    def boom(model: str, p: int, c: int) -> float | None:
        raise RuntimeError("no prices")

    row = await UsageLedger(services.db, boom).record("s", "r", "m", Usage(1, 1))
    assert row.cost_usd is None and row.estimated


async def test_runner_writes_one_usage_row_per_model_call(services: Services, gateway: FakeGateway) -> None:
    gateway.queue(Scripted(tool_calls=[("glob", {"pattern": "*"})]), Scripted(text="done"))
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "look", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    rows = await services.coordinator.runner.usage.by_session(sess.id)
    assert len(rows) == 2 and {r.session_id for r in rows} == {sess.id}
    assert all(r.model == "default" and r.prompt_tokens > 0 for r in rows)


async def test_budget_blocks_turn_without_running_tools(services: Services, gateway: FakeGateway) -> None:
    services.settings.max_cost_usd = 0.0  # the fake reports $0.00 per call: first call reaches it
    gateway.queue(Scripted(text="partial", tool_calls=[("glob", {"pattern": "*"})]))
    sess = await services.create_session()
    rid = await services.coordinator.submit(sess.id, "spend", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    result = services.coordinator.result(rid or "")
    assert result is not None and result.status == "blocked" and result.error
    assert result.error["code"] == "BUDGET" and result.text == "partial"
    hist = await services.coordinator.runner.load_history(sess.id)
    assert [m["role"] for m in hist] == ["user", "assistant"] and "tool_calls" not in hist[1]
    events = await services.events.replay(0, frozenset({f"session:{sess.id}"}))
    blocked = [e for e in events if e.type == "run.blocked"]
    assert blocked and blocked[0].data["error"]["code"] == "BUDGET"


async def test_budget_not_reached_runs_normally(services: Services) -> None:
    services.settings.max_cost_usd = 5.0
    sess = await services.create_session()
    rid = await services.coordinator.submit(sess.id, "hi", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    result = services.coordinator.result(rid or "")
    assert result is not None and result.status == "completed"


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c


async def test_usage_endpoints(client: httpx.AsyncClient, services: Services) -> None:
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "hello", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    other = await services.create_session()
    await services.coordinator.submit(other.id, "x", interactive=False)
    await services.coordinator.wait(other.id, 10)
    rows = (await client.get("/api/usage", params={"session_id": sess.id})).json()
    assert len(rows) == 1 and rows[0]["session_id"] == sess.id and rows[0]["cost_usd"] == 0.0
    assert len((await client.get("/api/usage", params={"limit": 10})).json()) == 2
    summary = (await client.get("/api/usage/summary", params={"days": 7})).json()
    assert summary["days"] == 7 and summary["total"]["calls"] == 2
    assert summary["by_model"][0]["key"] == "default" and len(summary["by_session"]) == 2


def test_cli_stats_and_db_commands() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["stats", "--days", "3", "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["days"] == 3 and data["total"]["calls"] == 0
    r = runner.invoke(cli, ["stats"])
    assert r.exit_code == 0 and "usage by model" in r.output
    r = runner.invoke(cli, ["db", "upgrade"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(cli, ["db", "history"])
    assert r.exit_code == 0 and "0004" in r.output and "0001" in r.output
    r = runner.invoke(cli, ["db", "current"])
    assert r.exit_code == 0 and "0004 (head)" in r.output
