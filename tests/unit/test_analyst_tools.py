"""Analyst tools (W1-B): decision_matrix ranking + sensitivity, scenario_sim determinism and
its expression sandbox; plus the analyst agent's tool set."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from auoreo.core.services import Services
from auoreo.harness.orchestrator import build_spec
from auoreo.tools.context import ToolError
from tests.teams_helpers import call_tool, new_parent, tool_ctx

ROOT = Path(__file__).resolve().parents[2]


def load(tool: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_test_{tool}", ROOT / "tools" / tool / "handler.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dm = load("decision_matrix")
sim = load("scenario_sim")

CRITERIA = [{"name": "quality", "weight": 1}, {"name": "cost", "weight": 1, "direction": "min"}]
OPTIONS = [
    {"name": "A", "scores": {"quality": 9, "cost": 8}},
    {"name": "B", "scores": {"quality": 5, "cost": 2}},
]


def test_decision_matrix_ranks_with_normalised_weights_and_direction() -> None:
    r = dm.evaluate(CRITERIA, OPTIONS)
    assert r["weights"] == {"quality": 0.5, "cost": 0.5}
    assert r["effective"]["A"]["cost"] == 2 and r["effective"]["B"]["cost"] == 8
    assert r["totals"] == {"A": pytest.approx(5.5), "B": pytest.approx(6.5)}
    assert r["ranking"] == ["B", "A"]


def test_decision_matrix_sensitivity_finds_the_flipping_weight() -> None:
    r = dm.evaluate(CRITERIA, OPTIONS)
    by = {s["criterion"]: s for s in r["sensitivity"]}
    # A(w) = 2 + 7w, B(w) = 8 - 3w on the quality weight → tie at w = 0.6
    assert by["quality"]["at"] == pytest.approx(0.6) and by["quality"]["challenger"] == "A"
    assert by["cost"]["at"] == pytest.approx(0.4) and by["cost"]["challenger"] == "A"
    md = dm.render(r, CRITERIA)
    assert md.splitlines()[0] == "| Rank | Option | quality (50.0%) | cost (50.0%) | Score |"
    assert "| 1 | B | 5.0 | 8.0 | **6.50** |" in md and "**Winner: B**" in md
    assert "raising **quality** from 50.0% to 60.0%" in md and "makes **A** win" in md
    assert "lowering **cost** from 50.0% to 40.0%" in md


def test_decision_matrix_robust_winner_and_validation() -> None:
    criteria = [{"name": "x", "weight": 2}, {"name": "y", "weight": 6}]
    options = [{"name": "dom", "scores": {"x": 9, "y": 9}}, {"name": "sub", "scores": {"x": 3, "y": 4}}]
    r = dm.evaluate(criteria, options)
    assert r["weights"] == {"x": 0.25, "y": 0.75}
    assert all(s["at"] is None for s in r["sensitivity"])
    assert "no single weight change on x, y flips the winner" in dm.render(r, criteria)
    with pytest.raises(ToolError, match="missing scores"):
        dm.evaluate(criteria, [{"name": "a", "scores": {"x": 1}}, options[0]])
    with pytest.raises(ToolError, match="0–10"):
        dm.evaluate(criteria, [{"name": "a", "scores": {"x": 11, "y": 1}}, options[0]])
    with pytest.raises(ToolError, match="unique"):
        dm.evaluate(criteria, [options[0], options[0]])


VARS = {
    "units": {"dist": "triangular", "low": 800, "high": 1500, "mode": 1000},
    "price": {"dist": "normal", "mean": 20, "sd": 2},
    "fixed": {"dist": "choice", "values": [9000, 12000], "weights": [3, 1]},
    "unit_cost": {"dist": "uniform", "low": 6, "high": 9},
}
EXPR = "units * (price - unit_cost) - fixed"


def test_scenario_sim_is_deterministic_for_a_seed() -> None:
    a = sim.simulate(VARS, EXPR, iterations=5000, seed=7, threshold=0)
    b = sim.simulate(VARS, EXPR, iterations=5000, seed=7, threshold=0)
    c = sim.simulate(VARS, EXPR, iterations=5000, seed=8, threshold=0)
    assert a == b and a["mean"] != c["mean"]
    assert a["min"] <= a["p5"] <= a["p50"] <= a["p95"] <= a["max"]
    assert 0 < a["p_above"] < 1
    # E[units]≈1100, E[margin]=12.5, E[fixed]=9750 → mean ≈ 4000
    assert 3000 < a["mean"] < 5000


def test_scenario_sim_statistics_and_limits() -> None:
    r = sim.simulate(
        {"x": {"dist": "uniform", "low": 0, "high": 10}}, "x", iterations=100_000, seed=1, threshold=9
    )
    assert r["mean"] == pytest.approx(5, abs=0.05) and r["p50"] == pytest.approx(5, abs=0.1)
    assert r["p5"] == pytest.approx(0.5, abs=0.05) and r["p95"] == pytest.approx(9.5, abs=0.05)
    assert r["p_above"] == pytest.approx(0.1, abs=0.01)
    with pytest.raises(ToolError, match="iterations"):
        sim.simulate({"x": {"dist": "uniform", "low": 0, "high": 1}}, "x", iterations=100_001)
    with pytest.raises(ToolError, match="needs mean, sd"):
        sim.simulate({"x": {"dist": "normal"}}, "x")
    with pytest.raises(ToolError, match="low ≤ mode ≤ high"):
        sim.simulate({"x": {"dist": "triangular", "low": 0, "high": 1, "mode": 5}}, "x")
    with pytest.raises(ToolError, match="invalid variable names"):
        sim.simulate({"Bad": {"dist": "uniform", "low": 0, "high": 1}}, "Bad")
    out = sim.simulate(
        {"x": {"dist": "choice", "values": [2]}}, "round(max(x, 1) ** 2 / 3, 2) + abs(-x) + min(x, 0)"
    )
    assert out["mean"] == pytest.approx(1.33 + 2)


@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ("__import__('os').system('true')", "calls are limited"),
        ("x.real", "attribute access"),
        ("x.__class__", "attribute access"),
        ("open('f')", "calls are limited"),
        ("(lambda: 1)()", "calls are limited"),
        ("max(x, key=abs)", "calls are limited"),
        ("[x][0]", "not allowed"),
        ("x if x else 1", "not allowed"),
        ("__builtins__", "unknown name"),
        ("y + 1", "unknown name"),
        ("'a' * 3", "numeric constants"),
        ("x // 2", "operator FloorDiv"),
        ("x and 1", "not allowed"),
        ("x +", "not valid arithmetic"),
    ],
)
def test_scenario_sim_expression_sandbox_rejects(expression: str, message: str) -> None:
    with pytest.raises(ToolError, match=message):
        sim.compile_expression(expression, {"x"})


def test_scenario_sim_overflow_is_an_error_not_a_hang() -> None:
    with pytest.raises(ToolError, match="failed at iteration 1"):
        sim.simulate({"x": {"dist": "choice", "values": [2]}}, "x ** 10 ** 10", iterations=3)
    with pytest.raises(ToolError, match="failed at iteration 1"):
        sim.simulate({"x": {"dist": "choice", "values": [0]}}, "1 / x", iterations=3)


async def test_tools_through_the_executor_and_analyst_agent(services: Services) -> None:
    ctx = tool_ctx(services, await new_parent(services))
    text, err = await call_tool(services, ctx, "decision_matrix", {"criteria": CRITERIA, "options": OPTIONS})
    assert not err and "**Winner: B**" in text
    args = {"variables": VARS, "expression": EXPR, "iterations": 2000, "seed": 3, "threshold": 0}
    text, err = await call_tool(services, ctx, "scenario_sim", args)
    assert not err and "2000 iterations, seed 3" in text and "P(expression > 0)" in text
    text, err = await call_tool(services, ctx, "scenario_sim", {**args, "iterations": 200_000})
    assert err
    text, err = await call_tool(services, ctx, "scenario_sim", {**args, "expression": "__import__('os')"})
    assert err and "calls are limited" in text

    spec = build_spec(services.harness, "analyst", "default", "manual")
    names = {t.name for t in (*spec.tools, *spec.deferred)}
    assert {"decision_matrix", "scenario_sim", "board_get", "board_put", "read", "web_search"} <= names
    assert {t.name for t in spec.deferred} >= {"decision_matrix", "scenario_sim", "board_get", "board_put"}
    assert "edit" not in names and "bash" not in names
    assert "decide" in services.harness.workflows
