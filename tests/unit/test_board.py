"""Team blackboard (W1-B): limits, versioning, secret refusal, non-team error, events, API."""

from __future__ import annotations

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import select

from auoreo.api.app import create_app
from auoreo.core.db import AuditRow
from auoreo.core.services import Services
from auoreo.gateway.fake import Scripted
from auoreo.gateway.types import text_of
from auoreo.teams.board import MAX_VALUE_BYTES
from auoreo.teams.service import TeamsError, TeamsService
from tests.teams_helpers import RoutedGateway, call_tool, new_parent, tool_ctx, turn


@pytest.fixture
def gateway() -> RoutedGateway:
    return RoutedGateway()


def teams_of(svc: Services) -> TeamsService:
    teams = svc.ext["teams"]
    assert isinstance(teams, TeamsService)
    return teams


async def board_events(svc: Services) -> list[tuple[str, dict]]:  # type: ignore[type-arg]
    evs = await svc.events.replay(0, frozenset({"server"}), limit=100_000)
    return [(e.type, e.data) for e in evs if e.type.startswith("board.")]


async def test_put_upserts_and_bumps_version_with_events(services: Services) -> None:
    teams = teams_of(services)
    lead = await new_parent(services)
    team = await teams.create_team(lead, "alpha")
    first = await teams.board.put(team.id, "results/task-1", "draft", "lead")
    assert (first.version, first.producer) == (1, "lead")
    second = await teams.board.put(team.id, "results/task-1", "final", "worker_1")
    assert (second.version, second.producer, second.value) == (2, "worker_1", "final")
    assert second.updated_at >= first.created_at
    await teams.board.put(team.id, "plan", "the plan", "lead")
    assert [r.key for r in await teams.board.list(team.id)] == ["plan", "results/task-1"]
    assert [r.key for r in await teams.board.list(team.id, "results/")] == ["results/task-1"]
    got = await teams.board.get(team.id, "results/task-1")
    assert got is not None and got.value == "final"
    assert await teams.board.get(team.id, "missing") is None
    puts = [d for t, d in await board_events(services) if t == "board.put"]
    assert [(d["key"], d["version"], d["producer"]) for d in puts] == [
        ("results/task-1", 1, "lead"),
        ("results/task-1", 2, "worker_1"),
        ("plan", 1, "lead"),
    ]
    assert puts[0]["team_id"] == team.id and puts[0]["bytes"] == 5
    assert await teams.board.delete(team.id, "plan", "lead") is True
    assert await teams.board.delete(team.id, "plan") is False
    assert ("board.delete", {"team_id": team.id, "key": "plan", "producer": "lead"}) in await board_events(
        services
    )
    # boards are per team
    other = await teams.create_team(await new_parent(services), "beta")
    assert await teams.board.list(other.id) == []


async def test_limits_and_key_pattern(services: Services) -> None:
    teams = teams_of(services)
    team = await teams.create_team(await new_parent(services), "limits")
    await teams.board.put(team.id, "big", "x" * MAX_VALUE_BYTES, "lead")  # exactly 64 KiB is fine
    with pytest.raises(TeamsError, match="64 KiB"):
        await teams.board.put(team.id, "big", "x" * (MAX_VALUE_BYTES + 1), "lead")
    with pytest.raises(TeamsError, match="64 KiB"):  # UTF-8 bytes, not characters
        await teams.board.put(team.id, "big", "é" * (MAX_VALUE_BYTES // 2 + 1), "lead")
    for bad in ["", "Upper", "-lead", "a" * 201, "a b", "a//b", "a/../b", "/abs"]:
        with pytest.raises(TeamsError, match="invalid board key"):
            await teams.board.put(team.id, bad, "v", "lead")
    await teams.board.put(team.id, "a" * 200, "v", "lead")
    await teams.board.put(team.id, "decision/use-postgres.v2", "v", "lead")


@pytest.mark.parametrize(
    "value",
    [
        "key: sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
        "OPENAI_API_KEY=abcdef123456",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----",
        "postgres://user:hunter22@db/app",
    ],
)
async def test_secret_values_are_refused(services: Services, value: str) -> None:
    teams = teams_of(services)
    team = await teams.create_team(await new_parent(services), "sec")
    with pytest.raises(TeamsError, match="secret"):
        await teams.board.put(team.id, "leak", value, "lead")
    assert await teams.board.list(team.id) == []


async def test_tools_resolve_the_callers_team(services: Services, gateway: RoutedGateway) -> None:
    teams = teams_of(services)
    loner = await new_parent(services)
    for name, args in [
        ("board_put", {"key": "k", "value": "v"}),
        ("board_get", {"key": "k"}),
        ("board_list", {}),
    ]:
        text, err = await call_tool(services, tool_ctx(services, loner), name, args)
        assert err and "not in a team" in text

    lead = await new_parent(services)
    kid = await teams.spawn(lead, agent="default", prompt="CHILD-BOARD hi", name="kid", background=False)
    text, err = await call_tool(
        services, tool_ctx(services, kid.session_id), "board_put", {"key": "results/kid", "value": "42 lines"}
    )
    assert not err and "board:results/kid v1" in text and "pointer" in text
    text, err = await call_tool(services, tool_ctx(services, lead), "board_get", {"key": "results/kid"})
    assert not err and "by kid" in text and text.endswith("42 lines")
    text, err = await call_tool(services, tool_ctx(services, lead), "board_list", {"prefix": "results/"})
    assert not err and "- results/kid v1 by kid" in text
    text, err = await call_tool(services, tool_ctx(services, lead), "board_get", {"key": "nope"})
    assert err and "no board key 'nope'" in text
    text, err = await call_tool(
        services,
        tool_ctx(services, lead),
        "board_put",
        {"key": "s", "value": "token=ghp_abcdefghijklmnop1234"},
    )
    assert err and "secret" in text


async def test_board_tools_are_deferred_and_loadable(services: Services, gateway: RoutedGateway) -> None:
    gateway.route(
        "BOARD-LOAD",
        Scripted(tool_calls=[("tool_search", {"query": "blackboard board put get list", "limit": 3})]),
        Scripted(tool_calls=[("board_put", {"key": "notes", "value": "hello board"})]),
        Scripted(text="done"),
    )
    lead = await new_parent(services)
    await teams_of(services).create_team(lead, "loaded")
    result = await turn(services, lead, "BOARD-LOAD go")
    assert result.status == "completed"
    first = gateway.requests_for("BOARD-LOAD")[0]
    assert "board_put" not in {t["function"]["name"] for t in first.tools}
    last = gateway.requests_for("BOARD-LOAD")[-1]
    tool_texts = [text_of(m["content"]) for m in last.messages if m.get("role") == "tool"]
    assert any("board:notes v1" in t for t in tool_texts)


async def test_board_api_list_get_put_audited(services: Services) -> None:
    teams = teams_of(services)
    team = await teams.create_team(await new_parent(services), "api")
    await teams.board.put(team.id, "results/a", "alpha", "kid")
    app = create_app(services.settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        rows = (await c.get(f"/api/teams/{team.id}/board", params={"prefix": "results/"})).json()
        assert [(r["key"], r["version"], r["producer"], r["bytes"]) for r in rows] == [
            ("results/a", 1, "kid", 5)
        ]
        assert "value" not in rows[0]
        got = (await c.get(f"/api/teams/{team.id}/board/results/a")).json()
        assert got["value"] == "alpha"
        put = await c.put(f"/api/teams/{team.id}/board/results/a", json={"value": "beta"})
        assert put.status_code == 200 and put.json()["version"] == 2 and put.json()["producer"] == "api"
        assert (await c.get(f"/api/teams/{team.id}/board/missing")).status_code == 404
        assert (await c.get("/api/teams/t_nope/board")).status_code == 404
        assert (await c.put(f"/api/teams/{team.id}/board/Bad", json={"value": "x"})).status_code == 422
        leak = await c.put(f"/api/teams/{team.id}/board/k", json={"value": "sk-proj-abcdefghijklmnop"})
        assert leak.status_code == 422
        big = await c.put(f"/api/teams/{team.id}/board/k", json={"value": "é" * 40_000})
        assert big.status_code == 413
        assert (await c.get(f"/api/teams/{team.id}/decisions")).json() == []
    async with services.db.session() as s:
        audit = [(r.action, r.decision) for r in (await s.execute(select(AuditRow))).scalars()]
    assert ("board.put", "allow") in audit and ("board.put", "deny") in audit
