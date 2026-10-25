"""Swarm + decide workflows end to end on the fake gateway with scripted tool calls.

Swarm: the coordinator step creates kanban tasks and the board plan, runs two implementer
workers that claim tasks and put results on the board, and the reviewer step (which joins
the coordinator's team) reads them and writes `summary/final`. Decide: researcher → analyst,
which records `decision/<slug>` on the board inside a team and answers in text outside one.
"""

from __future__ import annotations

import pytest

from auoreo.core.db import TeamMemberRow
from auoreo.core.services import Services
from auoreo.gateway.fake import Scripted
from auoreo.gateway.types import text_of
from auoreo.teams.service import TeamsService
from tests.teams_helpers import RoutedGateway, new_parent, turn


@pytest.fixture
def gateway() -> RoutedGateway:
    return RoutedGateway()


def teams_of(svc: Services) -> TeamsService:
    teams = svc.ext["teams"]
    assert isinstance(teams, TeamsService)
    return teams


def load(*names: str) -> Scripted:
    return Scripted(tool_calls=[("tool_search", {"query": n, "limit": 1}) for n in names])


def tool_texts(gateway: RoutedGateway, key: str) -> list[str]:
    last = gateway.requests_for(key)[-1]
    return [text_of(m["content"]) for m in last.messages if m.get("role") == "tool"]


def script_worker(gateway: RoutedGateway, n: int) -> None:
    me = f"worker_{n}"
    gateway.route(
        f"WORKER-{n}",
        load("kanban_claim", "kanban_complete", "board_put"),
        Scripted(tool_calls=[("kanban_claim", {"board": "swarm", "worker": me})]),
        Scripted(
            tool_calls=[
                ("board_put", {"key": f"results/task-{n}", "value": f"widget {n} built by {me}"}),
                ("kanban_complete", {"task_id": n, "worker": me, "summary": f"board:results/task-{n}"}),
            ]
        ),
        Scripted(text=f"done board:results/task-{n}"),
    )


async def test_swarm_workflow_end_to_end(services: Services, gateway: RoutedGateway) -> None:
    script_worker(gateway, 1)
    script_worker(gateway, 2)
    gateway.route(
        "Review the swarm results",
        load("board_list", "board_get", "board_put"),
        Scripted(
            tool_calls=[
                ("board_list", {"prefix": "results/"}),
                ("board_get", {"key": "plan"}),
                ("board_get", {"key": "results/task-1"}),
                ("board_get", {"key": "results/task-2"}),
            ]
        ),
        Scripted(tool_calls=[("board_put", {"key": "summary/final", "value": "approve: 2/2 widgets built"})]),
        Scripted(text="approve board:summary/final"),
    )
    worker_goal = "WORKER-{n}: claim tasks from kanban board swarm as worker_{n}; results go to the board."
    gateway.route(
        "swarm coordinator",
        load("kanban_create", "board_put"),
        Scripted(
            tool_calls=[
                ("team_create", {"name": "widgets"}),
                ("kanban_create", {"board": "swarm", "title": "widget 1", "body": "result: results/task-1"}),
                ("kanban_create", {"board": "swarm", "title": "widget 2", "body": "result: results/task-2"}),
                ("board_put", {"key": "plan", "value": "task 1 → results/task-1; task 2 → results/task-2"}),
            ]
        ),
        Scripted(
            tool_calls=[
                (
                    "delegate_task",
                    {"goal": worker_goal.format(n=1), "agent": "implementer", "name": "worker_1"},
                ),
                (
                    "delegate_task",
                    {"goal": worker_goal.format(n=2), "agent": "implementer", "name": "worker_2"},
                ),
            ]
        ),
        Scripted(text="COORD DONE: results/task-1, results/task-2"),
    )

    wf = await services.create_session(title="swarm", workflow="swarm")
    result = await turn(services, wf.id, "SWARM-GOAL build two widgets", timeout=60)
    assert result.status == "completed" and result.text == "approve board:summary/final"

    steps = {s.title: s for s in await services.list_sessions() if s.parent_id == wf.id}
    assert set(steps) == {"swarm:plan", "swarm:review"}
    teams = teams_of(services)
    team = await teams.team_of(steps["swarm:plan"].id)
    assert team is not None and team.lead_session_id == steps["swarm:plan"].id
    members = {m.name: m for m in await teams.members(team.id)}
    assert set(members) == {"worker_1", "worker_2", "review"}
    assert members["worker_1"].agent == "implementer" and members["worker_1"].depth == 2
    assert members["review"].session_id == steps["swarm:review"].id

    board = {r.key: r for r in await teams.board.list(team.id)}
    assert set(board) == {"plan", "results/task-1", "results/task-2", "summary/final"}
    assert board["plan"].producer == "lead"
    assert (board["results/task-1"].producer, board["results/task-2"].producer) == ("worker_1", "worker_2")
    assert board["summary/final"].producer == "review" and board["summary/final"].value.startswith("approve")

    tasks = await teams.kanban.list_tasks("swarm")
    assert [(t.title, t.status, t.worker) for t in tasks] == [
        ("widget 1", "done", "worker_1"),
        ("widget 2", "done", "worker_2"),
    ]
    seen = "\n".join(tool_texts(gateway, "Review the swarm results"))
    assert "widget 1 built by worker_1" in seen and "widget 2 built by worker_2" in seen
    assert "2 key(s)" in seen and "board:plan v1 by lead" in seen
    # the mailbox stayed control-plane: nothing was mailed
    assert await teams.mail.messages(team.id) == []


def script_decide(gateway: RoutedGateway, *, board: bool) -> None:
    gateway.route("Gather what is needed", Scripted(text="Options: A (cheap), B (fast). Cost 6–9k."))
    analysis = [
        load("decision_matrix", "board_put"),
        Scripted(
            tool_calls=[
                (
                    "decision_matrix",
                    {
                        "criteria": [
                            {"name": "cost", "weight": 2, "direction": "min"},
                            {"name": "speed", "weight": 1},
                        ],
                        "options": [
                            {"name": "A", "scores": {"cost": 3, "speed": 4}},
                            {"name": "B", "scores": {"cost": 8, "speed": 9}},
                        ],
                    },
                )
            ]
        ),
        Scripted(
            tool_calls=[
                ("board_put", {"key": "decision/a-or-b", "value": "Pick A: cheaper; flips if speed > 64%"})
            ]
        ),
    ]
    final = "board:decision/a-or-b pick A, cheaper" if board else "Recommendation: A (board unavailable)"
    gateway.route("Make this decision", *analysis, Scripted(text=final))


async def test_decide_workflow_outside_a_team_answers_in_text(
    services: Services, gateway: RoutedGateway
) -> None:
    script_decide(gateway, board=False)
    wf = await services.create_session(title="decide", workflow="decide")
    result = await turn(services, wf.id, "A or B?", timeout=30)
    assert result.status == "completed" and result.text.startswith("Recommendation: A")
    texts = tool_texts(gateway, "Make this decision")
    assert any("**Winner: A**" in t for t in texts)
    assert any("not in a team" in t for t in texts)
    assert await teams_of(services).list_teams() == []


async def test_decide_workflow_inside_a_team_records_the_decision(
    services: Services, gateway: RoutedGateway
) -> None:
    script_decide(gateway, board=True)
    teams = teams_of(services)
    lead = await new_parent(services)
    team = await teams.create_team(lead, "deciders")
    wf = await services.create_session(title="decide", workflow="decide", parent_id=lead)
    # the session running the workflow is a member of the lead's team; its steps join it
    async with services.db.session() as s:
        s.add(TeamMemberRow(team_id=team.id, name="decider", agent="default", session_id=wf.id, depth=1))
        await s.commit()
    result = await turn(services, wf.id, "A or B?", timeout=30)
    assert result.status == "completed" and result.text.startswith("board:decision/a-or-b")
    row = await teams.board.get(team.id, "decision/a-or-b")
    assert row is not None and row.producer == "analyze" and row.value.startswith("Pick A")
    # steps join lazily, on their first team tool call (the researcher never made one)
    assert {m.name for m in await teams.members(team.id)} == {"decider", "analyze"}


async def test_swarm_and_decide_validate(services: Services) -> None:
    for name, agents in {"swarm": ["coordinator", "reviewer"], "decide": ["researcher", "analyst"]}.items():
        wf = services.harness.workflows[name].manifest
        assert [s.agent for s in wf.steps] == agents
